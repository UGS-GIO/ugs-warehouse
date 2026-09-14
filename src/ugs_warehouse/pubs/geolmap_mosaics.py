"""Build per-scale seamless RASTER mosaics of the published geologic maps as raster PMTiles.

The static-CDN equivalent of the old geolMapPortal ArcGIS Mosaic Datasets (MD_500K / MD_250K /
MD_24K ImageServers): the per-map COGs (geolmap/cogs/<series_id>.cog.tif, from pubs/harvest.py) are
grouped by their publication scale into three tiers and stitched into one raster PMTiles per tier —
geolmap/mosaics/geologic-maps-{tier}.pmtiles. The viewer toggles them like the old portal.

Pipeline per tier (GDAL, no full download — COGs are read in place over /vsigs):
  gdalbuildvrt (over /vsigs/<bucket>/...)  ->  gdal_translate -of MBTILES (lossless PNG tiles)
  ->  gdaladdo (overview = lower zooms)  ->  `pmtiles convert` (MBTiles -> PMTiles)  ->  upload

Source COGs are currently WebP-lossy (harvest COG_COMPRESS); tiles are encoded as PNG (lossless) so
this step adds NO further loss. If the harvest later switches to lossless COGs, just re-run this job.

    python -m ugs_warehouse.pubs.geolmap_mosaics --scale all
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

from ..core import config, gcs, stac
from . import identity, source
from .scale import DEFAULT_TIER, SCALE_LABEL, tier_of

PMTILES_MIME = config.PMTILES_MIME
# Each tier is one STAC item in this collection; the viewer toggles them like the old portal layers.
COLLECTION = "ugs-geologic-maps"
# Statewide extent (the mosaics are clipped to Utah). [W, S, E, N] in EPSG:4326.
UTAH_BBOX = [-114.053, 36.998, -109.041, 42.002]
UTAH_GEOM = {"type": "Polygon", "coordinates": [[
    [-114.053, 36.998], [-109.041, 36.998], [-109.041, 42.002],
    [-114.053, 42.002], [-114.053, 36.998]]]}
# Scale tiers (denominator upper bounds), matching the old MD_* mosaics. A map's scale is binned by
# its 1:N denominator: <=62.5k detail, <=350k intermediate, else overview.
TIERS = ("24k", "250k", "500k")
# Max web-mercator zoom per tier — the real fix for the build timeout. The COGs are 600 DPI, so
# GDAL's native max zoom is ~z18; tiling a STATEWIDE mosaic to z18 is astronomically many tiles and
# never finishes. Each tier is capped to the zoom its scale actually warrants (and where it's legible
# in the viewer): a 1:500k map adds nothing past ~z12, 24k past ~z14. Override with --maxzoom.
TIER_MAXZOOM = {"24k": 14, "250k": 12, "500k": 12}
# How far down to build overviews (lower zoom levels) off the capped base tiles.
OVERVIEW_LEVELS = ("2", "4", "8", "16", "32", "64", "128", "256", "512", "1024", "2048")


def mosaic_object(tier: str) -> str:
    return f"{identity.MOSAIC_PREFIX}/geologic-maps-{tier}.pmtiles"


def _cog_sids() -> set[str]:
    """Series ids that have a harvested COG (geolmap/cogs/<sid>.cog.tif)."""
    pfx = identity.COG_PREFIX.rstrip("/") + "/"
    out: set[str] = set()
    for path in gcs.list_paths(identity.COG_PREFIX):
        name = path[len(pfx):] if path.startswith(pfx) else path
        if name.endswith(".cog.tif"):
            out.add(name[: -len(".cog.tif")].upper())
    return out


def _group_by_tier() -> dict[str, list[str]]:
    """{tier -> [series_id, ...]} for every map that has a COG, binned by its publication scale."""
    scale_by_sid = {(p.get("series_id") or "").strip().upper(): (p.get("pub_scale") or "")
                    for p in source.read_pubs()}
    groups: dict[str, list[str]] = {t: [] for t in TIERS}
    unparsed = 0
    for sid in sorted(_cog_sids()):
        t = tier_of(scale_by_sid.get(sid, "")) or DEFAULT_TIER
        if tier_of(scale_by_sid.get(sid, "")) is None:
            unparsed += 1
        groups[t].append(sid)
    counts = ", ".join(f"{t}={len(groups[t])}" for t in TIERS)
    print(f"[mosaics] COGs grouped by scale: {counts}  (unparseable -> {DEFAULT_TIER}: {unparsed})")
    return groups


def _vsigs(sid: str) -> str:
    """GDAL /vsigs path to a COG — read in place from the (private) bucket, no download."""
    return f"/vsigs/{config.BUCKET}/{identity.COG_PREFIX}/{sid}.cog.tif"


def build_tier(tier: str, sids: list[str], maxz: int | None = None) -> bool:
    """Stitch one tier's COGs into a raster PMTiles and upload it. Returns False if the tier is empty."""
    if not sids:
        print(f"[mosaics] {tier}: no COGs — skipping")
        return False
    with tempfile.TemporaryDirectory() as tmp:
        listfile = os.path.join(tmp, "cogs.txt")
        with open(listfile, "w") as fh:
            fh.write("\n".join(_vsigs(s) for s in sids) + "\n")
        vrt = os.path.join(tmp, f"{tier}.vrt")
        mbtiles = os.path.join(tmp, f"{tier}.mbtiles")
        pmtiles = os.path.join(tmp, f"{tier}.pmtiles")

        # Performance-tuned GDAL environment variables for high-throughput cloud storage reading.
        gdal_env = os.environ.copy()
        gdal_env.update({
            "GDAL_CACHEMAX": "4096",                         # Use 4 GB cache (out of 16 GB available on runner)
            "GDAL_NUM_THREADS": "ALL_CPUS",                  # Parallelize tile rendering and compression
            "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",     # Prevent redundant sequential GCS directory scans
            "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.tiff,.vrt", # Limit seeking of non-existent sidecar files
            "VSI_CACHE": "TRUE",                             # Enable GDAL VSI file caching
            "VSI_CACHE_SIZE": "536870912",                   # 512 MB chunk cache for remote files
            "GDAL_HTTP_MAX_RETRY": "10",                     # Keep connections resilient against transient hiccups
            "GDAL_HTTP_RETRY_DELAY": "1",
        })

        print(f"[mosaics] {tier}: VRT over {len(sids)} COGs")
        subprocess.run(["gdalbuildvrt", "-q", "-addalpha", "-input_file_list", listfile, vrt],
                       env=gdal_env, check=True)

        # Lossless PNG tiles (alpha → transparent gaps where no map covers). Cap the base zoom to the
        # tier's level (--maxzoom overrides) — without this the 600 DPI native zoom blows the build up.
        mz = maxz if maxz is not None else TIER_MAXZOOM.get(tier)
        tr = ["gdal_translate", "-of", "MBTILES", "-r", "bilinear", "-co", "TILE_FORMAT=PNG"]
        if mz is not None:
            tr += ["-co", f"ZOOM_LEVEL={mz}"]
        print(f"[mosaics] {tier}: rendering base tiles -> MBTiles (max zoom {mz})")
        subprocess.run([*tr, vrt, mbtiles], env=gdal_env, check=True)

        print(f"[mosaics] {tier}: building overviews (lower zooms)")
        subprocess.run(["gdaladdo", "-r", "bilinear", mbtiles, *OVERVIEW_LEVELS], env=gdal_env, check=True)

        print(f"[mosaics] {tier}: MBTiles -> PMTiles")
        subprocess.run(["pmtiles", "convert", mbtiles, pmtiles], check=True)

        obj = mosaic_object(tier)
        size_mb = os.path.getsize(pmtiles) // 1024 // 1024
        print(f"[mosaics] {tier}: uploading {size_mb} MB -> {obj}")
        gcs.upload(pmtiles, obj, content_type=PMTILES_MIME, cache_control=gcs.CACHE_MUTABLE)
        _write_item(tier, len(sids), obj)
        print(f"[mosaics] {tier}: done -> {config.public_url(obj)}")
        return True


def _write_item(tier: str, n_maps: int, obj: str) -> None:
    """STAC item for one mosaic tier. The raster PMTiles is a `visual` pmtiles ASSET (a vector layer
    would be a web-map LINK instead) — that's how the viewer tells a raster mosaic from vector tiles."""
    label = SCALE_LABEL.get(tier, tier)
    item = stac.build_item(
        item_id=f"geologic-maps-{tier}",
        collection=COLLECTION,
        geometry=UTAH_GEOM, bbox=UTAH_BBOX,
        datetime_iso=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        properties={"title": f"Utah Geologic Maps — {label} seamless mosaic",
                    "ugs:scale": tier, "ugs:map_count": n_maps},
        assets={"tiles": {"href": config.public_url(obj), "type": PMTILES_MIME,
                          "roles": ["visual"], "ugs:render": "raster",
                          "title": f"Raster PMTiles ({label})"}},
        proj_epsg=4326,
    )
    stac.write_item(item)


def build(scales: list[str], maxz: int | None = None) -> int:
    groups = _group_by_tier()
    built = 0
    for tier in scales:
        if build_tier(tier, groups.get(tier, []), maxz=maxz):
            built += 1
    if built:
        print("[mosaics] refreshing STAC catalog")
        stac.refresh_catalog()
    print(f"[mosaics] complete: {built}/{len(scales)} tiers built")
    return built


def main() -> int:
    ap = argparse.ArgumentParser(description="Build per-scale raster PMTiles mosaics of geologic maps")
    ap.add_argument("--scale", choices=(*TIERS, "all"), default="all",
                    help="Which scale tier to build (default all). 24k is the largest — run it alone "
                         "with more memory if needed.")
    ap.add_argument("--maxzoom", type=int, default=None,
                    help="Cap the base (native) zoom level; default lets GDAL pick from resolution.")
    args = ap.parse_args()
    scales = list(TIERS) if args.scale == "all" else [args.scale]
    return 0 if build(scales, maxz=args.maxzoom) >= 0 else 1


if __name__ == "__main__":
    sys.exit(main())
