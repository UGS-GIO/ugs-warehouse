"""Extract ALL vector feature classes AND non-spatial GeMS companion tables from GIS-bundle
publications -> Parquet on GCS.

Handles .shp files and every layer inside Esri file geodatabases (.gdb) — spatial (preserving
CRS and field names, written as GeoParquet) and non-spatial companion tables like
DescriptionOfMapUnits / CorrelationOfMapUnits (written as plain Parquet, no geometry). Raw
schema is preserved verbatim for both. Independent of the COG harvest, providing deep vector
layers (contacts, faults, folds, etc.) and their companion tables to GCS and STAC. A per-series
`_manifest.json` records which extracted labels are spatial vs tables, so downstream (STAC
ingest) doesn't have to guess from names.

    python -m ugs_warehouse.pubs.vectors M-299DM
    python -m ugs_warehouse.pubs.vectors --all
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import sys
import tempfile
import zipfile

from ..core import config, gcs
from . import geoparquet, harvest, identity, source

VECTORS_PREFIX = os.environ.get("GEOLMAP_VECTORS_PREFIX", "geolmap/vectors")
PARQUET_MIME = config.PARQUET_MIME


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name)


def _sources(work: str) -> list[tuple[str, str | None, str, bool]]:
    """Find all vector AND non-spatial table sources in the extracted bundle directory.

    Each entry is a tuple of: (file_path, layer_name_or_None, layer_label, is_spatial).
    Non-spatial GeMS companion tables (DescriptionOfMapUnits, CorrelationOfMapUnits, ...) are
    included too, tagged is_spatial=False, so callers can branch by type instead of guessing
    from the label. A .shp always carries geometry, so shapefile entries are always spatial.
    """
    import pyogrio

    out: list[tuple[str, str | None, str, bool]] = []
    for shp in glob.glob(os.path.join(work, "**", "*.shp"), recursive=True):
        out.append((shp, None, _safe(os.path.basename(shp)[:-4]), True))

    for gdb in glob.glob(os.path.join(work, "**", "*.gdb"), recursive=True):
        if "template" in os.path.basename(gdb).lower():
            continue
        gstem = _safe(os.path.basename(gdb)[:-4])
        try:
            for name, geomtype in pyogrio.list_layers(gdb):
                out.append((gdb, name, f"{gstem}__{_safe(name)}", bool(geomtype)))
        except Exception:
            pass
    return out


def _extract_and_upload(
    work: str, series_id: str, srcs: list[tuple[str, str | None, str, bool]],
) -> dict[str, list[str]]:
    """Read every source in `srcs`, upload each non-empty layer/table as Parquet to
    `{VECTORS_PREFIX}/{series_id}/{label}.parquet`, and write the per-series manifest recording
    which labels are spatial vs plain tables. Returns that manifest:
    {"spatial": [...], "tables": [...]}.

    Spatial layers keep their geometry (GeoParquet via gpio, plus a bbox column). Non-spatial GeMS
    companion tables are read WITHOUT geometry via pyogrio and written as plain Parquet. Raw
    schema is preserved verbatim either way — no rename, no type coercion. A layer with 0 rows
    is skipped (as before); a layer whose reader raises is logged and skipped, same as today.
    """
    import geopandas as gpd
    import pyogrio

    manifest: dict[str, list[str]] = {"spatial": [], "tables": []}
    for path, layer, label, is_spatial in srcs:
        dst = os.path.join(work, f"{label}.parquet")
        try:
            if is_spatial:
                gdf = gpd.read_file(path, layer=layer, engine="pyogrio")
                if len(gdf) == 0:
                    continue
                geoparquet.write(gdf, dst)
            else:
                df = pyogrio.read_dataframe(path, layer=layer, read_geometry=False)
                if len(df) == 0:
                    continue
                df.to_parquet(dst)

            gcs_path = f"{VECTORS_PREFIX}/{series_id}/{label}.parquet"
            gcs.upload(dst, gcs_path, content_type=PARQUET_MIME, cache_control=gcs.CACHE_MUTABLE)
            manifest["spatial" if is_spatial else "tables"].append(label)
        except Exception as e:
            print(f"  layer {label} failed: {e}", file=sys.stderr)

    _write_manifest(series_id, manifest)
    return manifest


def _write_manifest(series_id: str, manifest: dict[str, list[str]]) -> None:
    """Also written empty, for a zip with nothing to extract: it marks the pub done, so later runs
    skip it instead of downloading the zip again. `--force` retries it."""
    gcs.put_bytes(json.dumps(manifest).encode(), f"{VECTORS_PREFIX}/{series_id}/_manifest.json",
                  content_type="application/json", cache_control=gcs.CACHE_MUTABLE)


def extracted_series() -> set[str]:
    """Series ids that already have layers on GCS, from one listing of the vectors prefix."""
    pfx = VECTORS_PREFIX.rstrip("/") + "/"
    return {p.removeprefix(pfx).split("/", 1)[0] for p in gcs.list_paths(pfx)}


def extract_one(series_id: str, dry_run: bool = False, force: bool = False,
                existing: set[str] | None = None) -> str:
    """Extract and upload all vector layers and non-spatial companion tables for a given
    series_id.

    Returns: 'ok', 'skip', or 'fail:*'.
    """
    pub = identity.Pub.parse(series_id)
    series_id = pub.series_id

    if "XXXX" in series_id:
        print(f"{series_id}: SKIP (unpublished placeholder)")
        return "skip"

    # `existing` is one listing for a batch run; a single pub lists its own prefix.
    done = (series_id in existing) if existing is not None \
        else bool(gcs.list_paths(f"{VECTORS_PREFIX}/{series_id}/"))
    if not force and not dry_run and done:
        print(f"{series_id}: SKIP (vector parquets already exist on GCS)")
        return "skip"

    _, gis_url = harvest.zip_urls(series_id)

    if not gis_url:
        print(f"{series_id}: SKIP (no GIS zip URL)")
        return "skip"

    if dry_run:
        print(f"[dry-run] {series_id}: would download {gis_url} and extract vector layers")
        return "ok"

    work = tempfile.mkdtemp(prefix=f"v_{series_id.replace('/', '_')}_")
    try:
        zp = os.path.join(work, "gis.zip")
        harvest.download(harvest.encode_url(gis_url), zp)
        with zipfile.ZipFile(zp) as z:
            z.extractall(work)

        srcs = _sources(work)
        if not srcs:
            _write_manifest(series_id, {"spatial": [], "tables": []})
            print(f"{series_id}: SKIP (no shapefiles, GDB layers, or tables found)")
            return "skip"

        manifest = _extract_and_upload(work, series_id, srcs)
        n_spatial, n_tables = len(manifest["spatial"]), len(manifest["tables"])
        if n_spatial + n_tables == 0:
            print(f"{series_id}: SKIP (0 layers extracted successfully)")
            return "skip"

        print(f"{series_id}: OK ({n_spatial} vector layers + {n_tables} companion tables "
              f"uploaded to {VECTORS_PREFIX}/{series_id}/)")
        return "ok"
    except Exception as e:
        print(f"{series_id}: FAIL {e}", file=sys.stderr)
        return f"fail:{type(e).__name__}"
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="Extract GIS publication vector layers to GCS")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("series_id", nargs="*", default=[], help="Series ID(s) to extract")
    g.add_argument("--all", action="store_true", help="Extract all series IDs from metadata")
    ap.add_argument("--limit", type=int, default=None, help="Limit number of publications to extract")
    ap.add_argument("--dry-run", action="store_true", help="Dry run (check URLs and schema only)")
    ap.add_argument("--force", action="store_true", help="Force extraction even if artifacts exist")
    args = ap.parse_args()

    sids = []
    existing = None
    if args.all:
        print(f"Loading publications from source: {source.source_name()}")
        all_sids = [s for p in source.read_pubs() if (s := (p.get("series_id") or "").strip())]
        # A batch run only visits pubs whose attachments list a GIS zip, and checks what is already
        # extracted with one listing, so a weekly run costs about nothing when nothing is new.
        sids = [s for s in all_sids if harvest._get_attached_zips(s)[1]]
        existing = None if args.force else extracted_series()
        print(f"Found {len(all_sids)} publications, {len(sids)} with a GIS zip, "
              f"{len(set(sids) & (existing or set()))} already extracted")
    else:
        sids = args.series_id

    if args.limit:
        sids = sids[:args.limit]

    rc = 0
    for sid in sids:
        res = extract_one(sid, dry_run=args.dry_run, force=args.force, existing=existing)
        if res.startswith("fail"):
            rc |= 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
