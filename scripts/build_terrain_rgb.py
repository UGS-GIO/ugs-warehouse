#!/usr/bin/env python
"""Build a self-hosted terrain-RGB tileset from a high-res DEM (e.g. UGRC 1 m lidar) and upload it
to the CDN, so the 3D fence viewer can use a per-pub lidar DEM instead of the global ~10 m terrarium.

Output: mapbox-encoded terrain-RGB PNG tiles at
    geolmap/terrain/<series_id>/{z}/{x}/{y}.png
served by the path-preserving CDN. The matching STAC asset (printed at the end) is:
    "lidar_terrain": { "href": "<cdn>/geolmap/terrain/<id>/{z}/{x}/{y}.png", "roles": ["terrain-rgb"], ... }
The viewer (Browse.tsx ThreeDViewer) detects role "terrain-rgb" and wires it as a maplibre
raster-dem source with encoding="mapbox".

Why mapbox encoding (not terrarium): rio-rgbify defaults (base=-10000, interval=0.1) ARE the mapbox
terrain-RGB scheme, and the viewer source is set to encoding="mapbox" to match.

DEM input: pass a local GeoTIFF or a gs:// path to the bare-earth DEM for the quad. Fetching the DEM
from UGRC is a manual/staged step — grab the project DEM from https://raster.utah.gov (or the lidar
project page) and hand it in via --dem. (Single quad ≈ a few MB of tiles.)

Deps (work-box image): gdal (gdalwarp), rio-rgbify, mb-util OR the bundled mbtiles exploder below.
Needs CDN write perms (same runtime SA as the warehouse jobs).

Usage:
    python -m scripts.build_terrain_rgb --dem OFR-778DM_dem.tif --id OFR-778DM --min-zoom 9 --max-zoom 16
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

# Importable without the heavy deps so --help / dry-run work anywhere.
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from ugs_warehouse.core import config, gcs  # noqa: E402

TERRAIN_PREFIX = "geolmap/terrain"  # CDN object prefix; path-preserved to the CDN URL


def _run(cmd: list[str]) -> None:
    print(f"  $ {' '.join(cmd)}")
    subprocess.run(cmd, check=True)


def _localize(dem: str, tmp: str) -> str:
    """Bring a gs:// DEM local; pass through a local path unchanged."""
    if dem.startswith("gs://"):
        local = os.path.join(tmp, "dem_src.tif")
        _run(["gcloud", "storage", "cp", dem, local])
        return local
    if not os.path.exists(dem):
        sys.exit(f"DEM not found: {dem}")
    return dem


def _explode_mbtiles(mbtiles: str, out_dir: str) -> int:
    """Write XYZ PNGs from an mbtiles. mbtiles stores TMS y; flip to XYZ (y = 2^z - 1 - tms_y).
    Returns the tile count. Tiles are copied verbatim — NO resampling, so the RGB-encoded elevation
    is preserved exactly (resampling terrain-RGB would interpolate the encoding into garbage)."""
    con = sqlite3.connect(mbtiles)
    rows = con.execute("SELECT zoom_level, tile_column, tile_row, tile_data FROM tiles").fetchall()
    for z, x, tms_y, blob in rows:
        y = (1 << z) - 1 - tms_y
        p = Path(out_dir) / str(z) / str(x)
        p.mkdir(parents=True, exist_ok=True)
        (p / f"{y}.png").write_bytes(blob)
    con.close()
    return len(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description="Build + upload a terrain-RGB tileset from a DEM")
    ap.add_argument("--dem", required=True, help="DEM GeoTIFF (local path or gs:// URI), any CRS")
    ap.add_argument("--id", required=True, help="series id / item id, e.g. OFR-778DM")
    ap.add_argument("--min-zoom", type=int, default=9)
    ap.add_argument("--max-zoom", type=int, default=16)
    ap.add_argument("--dry-run", action="store_true", help="build tiles locally; skip the CDN upload")
    args = ap.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        dem = _localize(args.dem, tmp)

        # 1. Reproject to web mercator (the tiling CRS). bilinear is fine here — we resample the raw
        #    ELEVATION, before RGB-encoding, so no encoding corruption.
        warped = os.path.join(tmp, "dem_3857.tif")
        print("-> warping DEM to EPSG:3857 …")
        _run(["gdalwarp", "-t_srs", "EPSG:3857", "-r", "bilinear", "-overwrite",
              "-co", "COMPRESS=DEFLATE", dem, warped])

        # 2. Encode + tile to mapbox terrain-RGB mbtiles (rio-rgbify: base=-10000, interval=0.1).
        mbtiles = os.path.join(tmp, "terrainrgb.mbtiles")
        print("-> encoding terrain-RGB + tiling …")
        _run(["rio", "rgbify", "-b", "-10000", "-i", "0.1",
              "--min-z", str(args.min_zoom), "--max-z", str(args.max_zoom),
              "-j", str(os.cpu_count() or 4), "--format", "png", warped, mbtiles])

        # 3. Explode mbtiles → XYZ PNGs (TMS→XYZ y-flip, verbatim tile bytes).
        out_dir = os.path.join(tmp, "xyz")
        n = _explode_mbtiles(mbtiles, out_dir)
        print(f"-> {n} terrain-RGB tiles (z{args.min_zoom}–{args.max_zoom})")

        prefix = f"{TERRAIN_PREFIX}/{args.id}"
        tile_template = config.public_url(f"{prefix}/{{z}}/{{x}}/{{y}}.png")

        if args.dry_run:
            print(f"DRY-RUN: built {n} tiles in {out_dir}; would upload to {prefix}/")
        else:
            # 4. Upload every tile (immutable — the id+path is the version). content-type image/png.
            print(f"-> uploading {n} tiles to {prefix}/ …")
            for png in Path(out_dir).rglob("*.png"):
                rel = png.relative_to(out_dir).as_posix()  # z/x/y.png
                gcs.upload(str(png), f"{prefix}/{rel}", content_type="image/png",
                           cache_control=gcs.CACHE_IMMUTABLE)
            print("  done.")

        # 5. The STAC asset to attach to the item (viewer keys off role "terrain-rgb").
        print("\nAdd this asset to the STAC item (key 'lidar_terrain'):")
        print('  {')
        print(f'    "href": "{tile_template}",')
        print('    "type": "image/png",')
        print('    "roles": ["terrain-rgb"],')
        print(f'    "title": "Lidar terrain-RGB DEM ({args.id})"')
        print('  }')
    return 0


if __name__ == "__main__":
    sys.exit(main())
