"""Georeference the 24k maps that have no GIS raster, as source rasters for the harvest.

`georef_quads.json` names each map's public PDF page or GeoTIFF zip and its control points: the
neatline corners in pixels of a `dpi` render, tied to the NAD27 corner coordinates printed on the
sheet (`bounds` = W S E N), or explicit `gcps` in `gcp_srs`. A map with a `world_file`, or none of
these, already carries its placement. Each map is warped to EPSG:26912 here, so the harvest needs
no NAD27 datum grid, then uploaded to its source raster object and recorded in `index.csv`.

    python -m scripts.georef_quads [SERIES ...] [--out DIR] [--dry-run] [--force]

Needs the GDAL and poppler CLIs, PROJ network access (NAD27 grid) and GCS write auth (ADC).
Then harvest each map: `gcloud run jobs execute geolmap-harvest --args=-m,ugs_warehouse.pubs.harvest,<series>`.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import requests

from ugs_warehouse.core import gcs
from ugs_warehouse.pubs import identity

MANIFEST = Path(__file__).with_suffix(".json")
TARGET_SRS = "EPSG:26912"
TIFF_MIME = "image/tiff; application=geotiff"
INDEX = f"{identity.SOURCES_PREFIX}/index.csv"


def gcp_args(m: dict, corner_srs: str) -> list[str]:
    """gdal_translate args that place the raw raster: GCPs + their CRS, [] when it is placed."""
    if "gcps" in m:
        pts, srs = m["gcps"], m["gcp_srs"]
    elif "corners" in m:
        w, s, e, n = m["bounds"]
        pts = [[*px, *ll] for px, ll in zip(m["corners"], ((w, n), (e, n), (e, s), (w, s)))]
        srs = corner_srs
    else:
        return []
    return ["-a_srs", srs, *(a for p in pts for a in ("-gcp", *map(str, p)))]


def warp_args(m: dict) -> list[str]:
    """Transform for the GCPs: exact through 4 corners, affine through 3, a fitted surface for many."""
    if "gcps" in m:
        return ["-order", "2", "-refine_gcps", "3", "20"]
    if "corners" in m:
        return ["-tps"] if len(m["corners"]) == 4 else ["-order", "1"]
    return []


def world_file_args(tfw: str, size: tuple[int, int], srs: str) -> list[str]:
    """-a_ullr/-a_srs from a world file, which gives the centre of the top-left pixel."""
    a, _, _, e, c, f = (float(v) for v in Path(tfw).read_text().split())
    x0, y0 = c - a / 2, f - e / 2
    return ["-a_srs", srs, "-a_ullr", *map(str, (x0, y0, x0 + size[0] * a, y0 + size[1] * e))]


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True, env={**os.environ, "PROJ_NETWORK": "ON"})


def fetch(url: str, dest: Path) -> None:
    with requests.get(url, stream=True, timeout=600) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            shutil.copyfileobj(r.raw, f)


def raw_raster(m: dict, dpi: int, work: Path) -> tuple[Path, list[str]]:
    """The map as a local raster, plus any args that place it."""
    src = work / Path(m["url"]).name
    fetch(m["url"], src)
    if "page" in m:
        out = work / "page"
        p = str(m["page"])
        run(["pdftoppm", "-r", str(dpi), "-f", p, "-l", p, "-singlefile", "-tiff",
             "-tiffcompression", "deflate", str(src), str(out)])
        src.unlink()
        return out.with_suffix(".tif"), []
    with zipfile.ZipFile(src) as z:
        tif = Path(z.extract(m["member"], work))
        if "world_file" not in m:
            return tif, []
        tfw = z.extract(m["world_file"], work)
    info = json.loads(subprocess.check_output(["gdalinfo", "-json", str(tif)]))
    return tif, world_file_args(tfw, tuple(info["size"]), m["srs"])


def georef(m: dict, dpi: int, corner_srs: str, work: Path, out: Path) -> None:
    raw, placed = raw_raster(m, dpi, work)
    vrt = work / "placed.vrt"
    run(["gdal_translate", "-q", "-of", "VRT", *(placed or gcp_args(m, corner_srs)), str(raw), str(vrt)])
    run(["gdalwarp", "-q", "-overwrite", *warp_args(m), "-t_srs", TARGET_SRS, "-r", "bilinear",
         "-multi", "-wo", "NUM_THREADS=ALL_CPUS", "-dstalpha", "-co", "TILED=YES",
         "-co", "COMPRESS=DEFLATE", "-co", "PREDICTOR=2", "-co", "BIGTIFF=IF_SAFER", str(vrt), str(out)])


def record(rows: dict[str, tuple[str, int]]) -> None:
    """Upsert this run's maps into the sources index."""
    index = {}
    if gcs.exists(INDEX):
        index = {r["series_id"]: r for r in csv.DictReader(io.StringIO(gcs.get_bytes(INDEX).decode()))}
    for sid, (source, size) in rows.items():
        index[sid] = {"series_id": sid, "source": source, "bytes": size}
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=["series_id", "source", "bytes"], lineterminator="\n")
    w.writeheader()
    w.writerows(index[k] for k in sorted(index))
    gcs.put_bytes(buf.getvalue().encode(), INDEX, content_type="text/csv", cache_control="no-cache")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("series", nargs="*", help="maps to process (default: all in the manifest)")
    ap.add_argument("--out", type=Path, help="keep the rasters in this folder")
    ap.add_argument("--dry-run", action="store_true", help="georeference only; upload nothing")
    ap.add_argument("--force", action="store_true", help="replace a source raster already in GCS")
    args = ap.parse_args(argv)
    spec = json.loads(MANIFEST.read_text())
    maps = [m for m in spec["maps"] if not args.series or m["series"] in args.series]
    if unknown := set(args.series) - {m["series"] for m in maps}:
        ap.error(f"not in the manifest: {', '.join(sorted(unknown))}")
    rc, done = 0, {}
    for m in maps:
        sid = m["series"]
        obj = identity.Pub(sid).source_raster_object
        if not (args.dry_run or args.force) and gcs.exists(obj):
            print(f"[georef] {sid}: SKIP {obj} exists")
            continue
        with tempfile.TemporaryDirectory(prefix=f"georef_{sid}_") as tmp:
            out = (args.out or Path(tmp)) / f"{sid}.tif"
            out.parent.mkdir(parents=True, exist_ok=True)
            try:
                georef(m, spec["dpi"], spec["corner_srs"], Path(tmp), out)
                if not args.dry_run:
                    gcs.upload(str(out), obj, content_type=TIFF_MIME)
                    page = f" p{m['page']}" if "page" in m else ""
                    done[sid] = (f"{m['url']}{page} via scripts/georef_quads.py", out.stat().st_size)
            except Exception as e:  # noqa: BLE001 — one bad map never stops the rest
                print(f"[georef] {sid}: FAIL {e}", file=sys.stderr)
                rc = 1
                continue
        print(f"[georef] {sid}: ok {out if args.out else obj}")
    if done:
        record(done)
    return rc


if __name__ == "__main__":
    sys.exit(main())
