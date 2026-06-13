"""Extract ALL vector feature classes from GIS-bundle publications -> GeoParquet on GCS.

Handles .shp files and spatial layers inside Esri file geodatabases (.gdb), preserving
CRS and field names. Independent of the COG harvest, providing deep vector layers
(contacts, faults, folds, etc.) to GCS and STAC.

    python -m ugs_warehouse.pubs.vectors M-299DM
    python -m ugs_warehouse.pubs.vectors --all
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import shutil
import sys
import tempfile
import zipfile

from ..core import gcs
from . import identity, source, harvest

VECTORS_PREFIX = os.environ.get("GEOLMAP_VECTORS_PREFIX", "geolmap/vectors")
PARQUET_MIME = "application/vnd.apache.parquet"


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name)


def _sources(work: str) -> list[tuple[str, str | None, str]]:
    """Find all vector sources in the extracted bundle directory.

    Each entry is a tuple of: (file_path, layer_name_or_None, layer_label).
    """
    import pyogrio

    out: list[tuple[str, str | None, str]] = []
    for shp in glob.glob(os.path.join(work, "**", "*.shp"), recursive=True):
        out.append((shp, None, _safe(os.path.basename(shp)[:-4])))

    for gdb in glob.glob(os.path.join(work, "**", "*.gdb"), recursive=True):
        if "template" in os.path.basename(gdb).lower():
            continue
        gstem = _safe(os.path.basename(gdb)[:-4])
        try:
            for name, geomtype in pyogrio.list_layers(gdb):
                if geomtype:  # spatial layers only
                    out.append((gdb, name, f"{gstem}__{_safe(name)}"))
        except Exception:
            pass
    return out


def extract_one(series_id: str, dry_run: bool = False, force: bool = False) -> str:
    """Extract and upload all vector layers for a given series_id.

    Returns: 'ok', 'skip', or 'fail:*'.
    """
    pub = identity.Pub.parse(series_id)
    series_id = pub.series_id

    if "XXXX" in series_id:
        print(f"{series_id}: SKIP (unpublished placeholder)")
        return "skip"

    # For vector extraction, we check if the directory prefix has objects on GCS
    gcs_dir = f"{VECTORS_PREFIX}/{series_id}/"
    if not force and not dry_run and list(gcs.list_paths(gcs_dir)):
        print(f"{series_id}: SKIP (vector parquets already exist on GCS)")
        return "skip"

    gt_url, gis_url = harvest.manifest_urls(series_id)
    if not gt_url and not gis_url:
        try:
            gt_url, gis_url = harvest.data_php_urls(series_id)
        except Exception:
            pass

    if not gis_url:
        print(f"{series_id}: SKIP (no GIS zip URL)")
        return "skip"

    if dry_run:
        print(f"[dry-run] {series_id}: would download {gis_url} and extract vector layers")
        return "ok"

    import geopandas as gpd

    work = tempfile.mkdtemp(prefix=f"v_{series_id.replace('/', '_')}_")
    try:
        zp = os.path.join(work, "gis.zip")
        harvest.download(harvest.encode_url(gis_url), zp)
        with zipfile.ZipFile(zp) as z:
            z.extractall(work)

        srcs = _sources(work)
        if not srcs:
            print(f"{series_id}: SKIP (no spatial shapefiles or GDB layers found)")
            return "skip"

        n_layers = 0
        for path, layer, label in srcs:
            dst = os.path.join(work, f"{label}.parquet")
            try:
                gdf = gpd.read_file(path, layer=layer, engine="pyogrio")
                if len(gdf) == 0:
                    continue
                gdf.to_parquet(dst)
                # Upload to GCS
                gcs_path = f"{VECTORS_PREFIX}/{series_id}/{label}.parquet"
                gcs.upload(dst, gcs_path, content_type=PARQUET_MIME, cache_control=gcs.CACHE_MUTABLE)
                n_layers += 1
            except Exception as e:
                print(f"  layer {label} failed: {e}", file=sys.stderr)

        if n_layers == 0:
            print(f"{series_id}: SKIP (0 spatial layers extracted successfully)")
            return "skip"

        print(f"{series_id}: OK ({n_layers} vector layers uploaded to {VECTORS_PREFIX}/{series_id}/)")
        return "ok"
    except Exception as e:
        print(f"{series_id}: FAIL {e}", file=sys.stderr)
        return f"fail:{type(e).__name__}"
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="Extract GIS publication vector layers to GCS")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("series_id", nargs="*", help="Series ID(s) to extract")
    g.add_argument("--all", action="store_true", help="Extract all series IDs from metadata")
    ap.add_argument("--limit", type=int, default=None, help="Limit number of publications to extract")
    ap.add_argument("--dry-run", action="store_true", help="Dry run (check URLs and schema only)")
    ap.add_argument("--force", action="store_true", help="Force extraction even if artifacts exist")
    args = ap.parse_args()

    sids = []
    if args.all:
        print(f"Loading publications from source: {source.source_name()}")
        pubs = source.read_pubs()
        for p in pubs:
            sid = (p.get("series_id") or "").strip()
            if sid:
                sids.append(sid)
        print(f"Found {len(sids)} publications")
    else:
        sids = args.series_id

    if args.limit:
        sids = sids[:args.limit]

    rc = 0
    for sid in sids:
        res = extract_one(sid, dry_run=args.dry_run, force=args.force)
        if res.startswith("fail"):
            rc |= 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
