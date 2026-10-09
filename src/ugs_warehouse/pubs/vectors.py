"""Extract ALL vector feature classes AND non-spatial GeMS companion tables from GIS-bundle
publications -> Parquet on GCS.

Handles .shp files and every layer inside Esri file geodatabases (.gdb) — spatial (preserving
CRS and field names, written as GeoParquet) and non-spatial companion tables like
DescriptionOfMapUnits / CorrelationOfMapUnits (written as plain Parquet, no geometry). Raw
schema is preserved verbatim for both. Independent of the COG harvest, providing deep vector
layers (contacts, faults, folds, etc.) and their companion tables to GCS and STAC. A per-series
`_manifest.json` records which extracted labels are spatial vs tables, so downstream (STAC
ingest) doesn't have to guess from names, plus each layer's rows, columns, CRS and WGS84 bbox,
the layers that were empty, and every layer or geodatabase that could not be read.

A pub with any unreadable layer returns `fail:layers` and the run exits non-zero, so a missing
layer never looks like a pub that simply has none.

    python -m ugs_warehouse.pubs.vectors M-299DM
    python -m ugs_warehouse.pubs.vectors --all
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import re
import shutil
import sys
import tempfile
import zipfile
from concurrent.futures import ThreadPoolExecutor

from ..core import config, gcs
from . import geoparquet, harvest, identity, source

VECTORS_PREFIX = os.environ.get("GEOLMAP_VECTORS_PREFIX", "geolmap/vectors")
PARQUET_MIME = config.PARQUET_MIME


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name)


def _error(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"


def _sources(work: str) -> tuple[list[tuple[str, str | None, str, bool]], list[dict]]:
    """Find all vector AND non-spatial table sources in the extracted bundle directory.

    Returns (sources, errors). Each source is a tuple of: (file_path, layer_name_or_None,
    layer_label, is_spatial). Non-spatial GeMS companion tables (DescriptionOfMapUnits,
    CorrelationOfMapUnits, ...) are included too, tagged is_spatial=False, so callers can branch
    by type instead of guessing from the label. A .shp always carries geometry, so shapefile
    entries are always spatial. A geodatabase whose layers cannot be listed is an error, not an
    empty bundle.
    """
    import pyogrio

    out: list[tuple[str, str | None, str, bool]] = []
    errors: list[dict] = []
    for shp in glob.glob(os.path.join(work, "**", "*.shp"), recursive=True):
        out.append((shp, None, _safe(os.path.basename(shp)[:-4]), True))

    for gdb in glob.glob(os.path.join(work, "**", "*.gdb"), recursive=True):
        if "template" in os.path.basename(gdb).lower():
            continue
        gstem = _safe(os.path.basename(gdb)[:-4])
        try:
            for name, geomtype in pyogrio.list_layers(gdb):
                out.append((gdb, name, f"{gstem}__{_safe(name)}", bool(geomtype)))
        except Exception as e:
            errors.append({"source": os.path.basename(gdb), "error": _error(e)})
            print(f"  geodatabase {os.path.basename(gdb)} unreadable: {e}", file=sys.stderr)
    return out, errors


def _table_type(arrow_type) -> str:
    """Arrow type → the STAC table extension type names the vector producer uses
    (vector/sink_stac.py `_table_type`), so every schema in the catalog reads the same way."""
    import pyarrow as pa

    t = arrow_type.value_type if pa.types.is_dictionary(arrow_type) else arrow_type
    if pa.types.is_integer(t):
        return "integer"
    if pa.types.is_floating(t) or pa.types.is_decimal(t):
        return "number"
    if pa.types.is_boolean(t):
        return "boolean"
    if pa.types.is_timestamp(t):
        return "datetime"
    if pa.types.is_date(t):
        return "date"
    if pa.types.is_string(t) or pa.types.is_large_string(t):
        return "string"
    return str(t)


def _schema(parquet_path: str) -> tuple[list[dict], dict | None]:
    """The written file's columns in the STAC table extension's shape ([{name, type}]) and its
    GeoParquet `geo` metadata, if any. Reading both from the file keeps it the single source."""
    import pyarrow.parquet as pq

    schema = pq.read_schema(parquet_path)
    meta = schema.metadata or {}
    geo = json.loads(meta[b"geo"]) if b"geo" in meta else None
    geom_cols = set((geo or {}).get("columns", {}))
    return [{"name": f.name, "type": "geometry" if f.name in geom_cols else _table_type(f.type)}
            for f in schema], geo


def _floor6(v: float) -> float:
    return math.floor(v * 1e6) / 1e6


def _ceil6(v: float) -> float:
    return math.ceil(v * 1e6) / 1e6


def _spatial_meta(gdf, geo: dict | None, label: str, warnings: list[dict]) -> dict:
    """Geometry types (from the written file's `geo` metadata), CRS as STAC projection
    `proj:code` / `proj:wkt2` values, and a WGS84 bbox. The EPSG code is recorded only for a
    confident match; the WKT2 is always recorded. A bbox that cannot be computed costs the layer
    its bbox, not its data, and is reported as a warning."""
    from pyproj import Transformer

    primary = (geo or {}).get("columns", {}).get((geo or {}).get("primary_column"), {})
    meta = {"geometry_types": primary.get("geometry_types", []),
            "proj_code": None, "proj_wkt2": None, "bbox": None}
    try:
        epsg = gdf.crs.to_epsg(min_confidence=90)
        meta["proj_code"] = f"EPSG:{epsg}" if epsg else None
        meta["proj_wkt2"] = gdf.crs.to_wkt()
    except Exception as e:
        warnings.append({"label": label, "warning": f"CRS not described: {_error(e)}"})
    bounds = [float(v) for v in gdf.total_bounds]
    if not all(math.isfinite(v) for v in bounds):
        warnings.append({"label": label, "warning": "every geometry is null or empty; bbox not computed"})
        return meta
    try:
        to_wgs84 = Transformer.from_crs(gdf.crs, "EPSG:4326", always_xy=True, allow_ballpark=False)
        west, south, east, north = to_wgs84.transform_bounds(*bounds, densify_pts=21, errcheck=True)
    except Exception as e:
        warnings.append({"label": label, "warning": f"bbox not computed: {_error(e)}"})
        return meta
    meta["bbox"] = [_floor6(west), _floor6(south), _ceil6(east), _ceil6(north)]
    try:
        area = gdf.crs.area_of_use
    except Exception as e:
        warnings.append({"label": label, "warning": f"CRS area of use unavailable: {_error(e)}"})
        return meta
    if area and (east < area.west or west > area.east or north < area.south or south > area.north):
        warnings.append({"label": label, "warning": f"bbox {meta['bbox']} is outside the CRS's "
                                                    f"area of use ({area.name}); the .prj may be wrong"})
    return meta


def _extract_and_upload(
    work: str, series_id: str, srcs: list[tuple[str, str | None, str, bool]],
    source_errors: list[dict] | None = None,
) -> dict:
    """Read every source in `srcs`, upload each non-empty layer/table as Parquet to
    `{VECTORS_PREFIX}/{series_id}/{label}.parquet`, and write the per-series manifest. Returns
    that manifest: {"spatial": [labels], "tables": [labels], "layers": [per-layer metadata],
    "empty": [labels], "errors": [...], "warnings": [...]}.

    Spatial layers keep their geometry (GeoParquet via gpio, plus a bbox column). Non-spatial GeMS
    companion tables are read WITHOUT geometry via pyogrio and written as plain Parquet. Raw
    schema is preserved verbatim either way — no rename, no type coercion. A layer with 0 rows is
    listed under "empty" and not uploaded. A layer that cannot be read or uploaded, or a spatial
    layer with no CRS, is listed under "errors" and the rest still extract. A CRS-less layer is
    never written: GeoParquet reads a missing CRS as longitude/latitude, so the file would
    mislabel its coordinates. The manifest is written only when at least one layer was uploaded.
    """
    import geopandas as gpd
    import pyogrio

    manifest: dict = {"spatial": [], "tables": [], "layers": [], "empty": [],
                      "errors": list(source_errors or []), "warnings": []}
    for path, layer, label, is_spatial in srcs:
        dst = os.path.join(work, f"{label}.parquet")
        try:
            if is_spatial:
                gdf = gpd.read_file(path, layer=layer, engine="pyogrio")
                rows = len(gdf)
                if rows and gdf.crs is None:
                    raise ValueError("no CRS (missing or unreadable .prj); not written, since "
                                     "GeoParquet would read its coordinates as longitude/latitude")
                if rows:
                    geoparquet.write(gdf, dst)
            else:
                df = pyogrio.read_dataframe(path, layer=layer, read_geometry=False)
                rows = len(df)
                if rows:
                    df.to_parquet(dst)
            if not rows:
                manifest["empty"].append(label)
                continue

            columns, geo = _schema(dst)
            entry = {"label": label, "spatial": is_spatial, "rows": rows, "columns": columns}
            if is_spatial:
                entry.update(_spatial_meta(gdf, geo, label, manifest["warnings"]))
            gcs_path = f"{VECTORS_PREFIX}/{series_id}/{label}.parquet"
            gcs.upload(dst, gcs_path, content_type=PARQUET_MIME, cache_control=gcs.CACHE_MUTABLE)
            manifest["spatial" if is_spatial else "tables"].append(label)
            manifest["layers"].append(entry)
        except Exception as e:
            manifest["errors"].append({"label": label, "error": _error(e)})
            print(f"  layer {label} failed: {e}", file=sys.stderr)

    if manifest["spatial"] or manifest["tables"]:
        gcs.put_bytes(json.dumps(manifest).encode(),
                       f"{VECTORS_PREFIX}/{series_id}/_manifest.json",
                       content_type="application/json", cache_control=gcs.CACHE_MUTABLE)

    return manifest


def _clean_manifest(path: str) -> bool:
    """True when the manifest at `path` reads as an object that records per-layer metadata and
    no errors. An unreadable manifest is reported and counts as not done, and so does one with no
    `layers` list, so ordinary resumable runs re-extract it instead of needing --force."""
    try:
        doc = json.loads(gcs.get_bytes(path).decode())
    except Exception as e:
        print(f"  unreadable manifest {path}, re-extracting: {_error(e)}", file=sys.stderr)
        return False
    if not isinstance(doc, dict):
        print(f"  manifest {path} is not an object, re-extracting", file=sys.stderr)
        return False
    return "layers" in doc and not doc.get("errors")


def extracted_series() -> set[str]:
    """Series ids extracted cleanly: a readable `_manifest.json` that lists no errors, from one
    listing of the vectors prefix. A series with errors, or with layers but no manifest, is left
    out, so every run retries it and reports it again instead of the failure going quiet."""
    pfx = VECTORS_PREFIX.rstrip("/") + "/"
    paths = [p for p in gcs.list_paths(pfx) if p.endswith("/_manifest.json")]
    with ThreadPoolExecutor(max_workers=16) as ex:
        clean = list(ex.map(_clean_manifest, paths))
    return {p.removeprefix(pfx).split("/", 1)[0] for p, ok in zip(paths, clean) if ok}


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

    if not force and not dry_run:
        # `existing` is one listing for a batch run; a single pub reads only its own manifest.
        if existing is not None:
            done = series_id in existing
        else:
            mpath = f"{VECTORS_PREFIX}/{series_id}/_manifest.json"
            done = mpath in gcs.list_paths(f"{VECTORS_PREFIX}/{series_id}/") and _clean_manifest(mpath)
        if done:
            print(f"{series_id}: SKIP (already extracted cleanly)")
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

        srcs, source_errors = _sources(work)
        if not srcs and not source_errors:
            print(f"{series_id}: SKIP (no shapefiles, GDB layers, or tables found)")
            return "skip"

        manifest = _extract_and_upload(work, series_id, srcs, source_errors)
        n_spatial, n_tables = len(manifest["spatial"]), len(manifest["tables"])
        errors = manifest["errors"]
        if errors:
            unreadable = ", ".join(e.get("label") or e["source"] for e in errors)
            print(f"{series_id}: FAIL ({n_spatial} vector layers + {n_tables} companion tables "
                  f"uploaded, {len(errors)} unreadable: {unreadable})", file=sys.stderr)
            return "fail:layers"
        if n_spatial + n_tables == 0:
            print(f"{series_id}: SKIP (every layer is empty)")
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

    failed: list[str] = []
    for sid in sids:
        res = extract_one(sid, dry_run=args.dry_run, force=args.force, existing=existing)
        if res.startswith("fail"):
            failed.append(f"{sid} ({res})")
    if failed:
        print(f"{len(failed)} of {len(sids)} publications failed: {', '.join(failed)}",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
