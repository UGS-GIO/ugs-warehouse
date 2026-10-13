"""Extract ALL vector feature classes AND non-spatial GeMS companion tables from GIS-bundle
publications -> Parquet on GCS.

Handles .shp files and every layer inside Esri file geodatabases (.gdb) — spatial (preserving
CRS and field names, written as GeoParquet) and non-spatial companion tables like
DescriptionOfMapUnits / CorrelationOfMapUnits (written as plain Parquet, no geometry). Raw
schema is preserved verbatim for both. Independent of the COG harvest, providing deep vector
layers (contacts, faults, folds, etc.) and their companion tables to GCS and STAC. A per-series
`_manifest.json` records which extracted labels are spatial vs tables, so downstream (STAC
ingest) doesn't have to guess from names, plus each layer's rows, columns, CRS and WGS84 bbox,
the layers that were empty, and every layer or geodatabase that could not be read. Each spatial
layer is also tiled to `<label>.pmtiles` (EPSG:4326, one tile layer named for the label) for map
display; its manifest entry records `pmtiles` and `pmtiles_layer`. A layer whose tiling fails
keeps its GeoParquet and gets a `pmtiles_failed` warning. Every `--all` run lists the layers its
clean manifests show still without PMTiles and exits non-zero while any remain;
`--pmtiles-backfill` builds them without a re-download.

A pub with any unreadable layer returns `fail:layers` and the run exits non-zero, so a missing
layer never looks like a pub that simply has none.

    python -m ugs_warehouse.pubs.vectors M-299DM
    python -m ugs_warehouse.pubs.vectors --all
    python -m ugs_warehouse.pubs.vectors --pmtiles-backfill [SID ...] [--force]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import sys
import tempfile
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor

from ..core import config, gcs
from ..vector import sink_pmtiles
from . import geoparquet, harvest, identity, source

VECTORS_PREFIX = os.environ.get("GEOLMAP_VECTORS_PREFIX", "geolmap/vectors")
PARQUET_MIME = config.PARQUET_MIME
# Warning code for a layer whose GeoParquet published but whose tiles did not. A warning, not an
# error, so the series stays clean and the batch run does not download its zip again.
PMTILES_FAILED = "pmtiles_failed"
# Warning code for a layer with no geometry to tile. Not a failure: it never gets PMTiles.
NO_GEOMETRY = "no_geometry"


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name)


def _error(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"


# GIS containers this image's GDAL cannot read. Finding one means the bundle holds data that is not
# extracted, which is reported rather than passed over as an empty bundle.
_UNSUPPORTED = {".mdb": "personal geodatabase (.mdb)", ".mpk": "map package (.mpk)",
                ".lpk": "layer package (.lpk)"}


def _walk(work: str) -> list[tuple[str, str]]:
    """(path, lowercase extension) for every file and directory under `work`, sorted. Extensions
    are compared lowercased because bundles mix `.shp` / `.SHP` and the job runs on Linux."""
    out = []
    for root, dirs, files in os.walk(work):
        out += [(os.path.join(root, n), os.path.splitext(n)[1].lower()) for n in dirs + files]
    return sorted(out)


# The job's disk is memory (tmpfs) inside its 8 GiB, so a bundle that would unzip past this is
# reported instead of extracted.
MAX_UNZIPPED_BYTES = int(float(os.environ.get("VECTORS_MAX_UNZIPPED_GB", "4")) * 2**30)


def _extract_zip(path: str, dest: str, remaining: int) -> int:
    """Extract `path` into `dest`, delete the zip, and return the bytes it unzipped to. Raises
    without extracting when that would exceed `remaining`."""
    with zipfile.ZipFile(path) as z:
        size = sum(i.file_size for i in z.infolist())
        if size > remaining:
            raise ValueError(f"unzips to {size / 2**30:.1f} GiB, over the "
                             f"{MAX_UNZIPPED_BYTES / 2**30:g} GiB limit for the job's in-memory disk")
        z.extractall(dest)
    os.remove(path)
    return size


def _nested_dest(path: str) -> str:
    """A folder for an inner zip's contents that does not exist yet, so nothing merges into an
    existing folder. `X.gdb.zip` holding bare geodatabase files extracts to `X.gdb` so it reads as
    a geodatabase; one holding an `X.gdb/` folder goes to `X.gdb.zip_unzipped/X.gdb`."""
    stem = os.path.splitext(path)[0]
    if stem.lower().endswith(".gdb") and not os.path.exists(stem):
        with zipfile.ZipFile(path) as z:
            if not any("/" in n.rstrip("/") for n in z.namelist()):
                return stem
    dest, n = f"{path}_unzipped", 2
    while os.path.exists(dest):
        dest, n = f"{path}_unzipped{n}", n + 1
    return dest


def _unzip_nested(work: str, remaining: int, depth: int = 2) -> tuple[list[dict], list[str]]:
    """Extract zips shipped inside the bundle into a new folder beside each one, so a zip of zips
    is searched like any other bundle. Returns (an error per inner zip that cannot be extracted or
    is nested deeper than `depth`, the folders extracted into)."""
    errors: list[dict] = []
    dirs: list[str] = []
    for _ in range(depth):
        inner = [p for p, ext in _walk(work) if ext == ".zip" and os.path.isfile(p)]
        if not inner:
            break
        for path in inner:
            dest = None
            try:
                dest = _nested_dest(path)
                remaining -= _extract_zip(path, dest, remaining)
                dirs.append(dest)
            except Exception as e:
                errors.append({"source": os.path.basename(path), "error": _error(e)})
                if dest:
                    shutil.rmtree(dest, ignore_errors=True)  # no half-extracted layers
                os.remove(path)
    for path, ext in _walk(work):
        if ext == ".zip" and os.path.isfile(path):
            errors.append({"source": os.path.basename(path),
                           "error": f"zip nested more than {depth} deep, not extracted"})
    return errors, dirs


def _sources(work: str, nested_dirs: list[str] | tuple = ()
             ) -> tuple[list[tuple[str, str | None, str, bool]], list[dict]]:
    """Find all vector AND non-spatial table sources in the extracted bundle directory.

    Returns (sources, errors). Each source is a tuple of: (file_path, layer_name_or_None,
    layer_label, is_spatial). Non-spatial GeMS companion tables (DescriptionOfMapUnits,
    CorrelationOfMapUnits, ...) are included too, tagged is_spatial=False, so callers can branch
    by type instead of guessing from the label. A .shp always carries geometry, so shapefile
    entries are always spatial. A geodatabase whose layers cannot be listed, or a container format
    GDAL cannot read here, is an error, not an empty bundle. Labels are unique, since two
    shapefiles with the same name in different folders would otherwise overwrite one upload. The
    outer bundle is labelled before the contents of inner zips (`nested_dirs`), so adding an
    inner zip never renames a layer that is already published; a repeated name takes its folder
    path inside the bundle as a prefix, or a numeric suffix at the bundle root.
    """
    import pyogrio

    out: list[tuple[str, str | None, str, bool]] = []
    errors: list[dict] = []
    used: set[str] = set()

    def unique(label: str, path: str) -> str:
        if label in used:
            rel = os.path.relpath(os.path.dirname(path), work)
            if rel != ".":
                label = f"{_safe(rel.replace(os.sep, '__'))}__{label}"
        base, n = label, 2
        while label in used:
            label, n = f"{base}_{n}", n + 1
        used.add(label)
        return label

    def nested(path: str) -> bool:
        return any(path == d or path.startswith(d + os.sep) for d in nested_dirs)

    entries = sorted(_walk(work), key=lambda e: (nested(e[0]), e[0]))
    for path, ext in entries:
        if ext == ".shp" and os.path.isfile(path):
            out.append((path, None, unique(_safe(os.path.basename(path)[:-4]), path), True))

    for path, ext in entries:
        name = os.path.basename(path)
        if ext == ".gdb" and os.path.isdir(path):
            if "template" in name.lower():
                continue
            gstem = _safe(name[:-4])
            try:
                for layer, geomtype in pyogrio.list_layers(path):
                    out.append((path, layer, unique(f"{gstem}__{_safe(layer)}", path), bool(geomtype)))
            except Exception as e:
                errors.append({"source": name, "error": _error(e)})
                print(f"  geodatabase {name} unreadable: {e}", file=sys.stderr)
        elif ext in _UNSUPPORTED:
            errors.append({"source": name, "error": f"unsupported format: {_UNSUPPORTED[ext]}, not extracted"})
            print(f"  {name}: unsupported format, not extracted", file=sys.stderr)
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


def pmtiles_object(series_id: str, label: str) -> str:
    return f"{VECTORS_PREFIX}/{series_id}/{label}.pmtiles"


def _tile_layer(gdf, series_id: str, label: str, tmp: str | None = None) -> bool:
    """Tile `gdf` as `<label>.pmtiles` beside its GeoParquet: reprojected to EPSG:4326 (tippecanoe
    reads lon/lat), one tile layer named `label`, built with the serving topics' tippecanoe flags.
    Returns False, writing nothing, when no row has a geometry. Temp files go under `tmp` and are
    deleted before returning, since the job's disk is memory. Holds at most `gdf` plus one
    reprojected copy, beyond a moment while rows without geometry are dropped."""
    blank = (gdf.geometry.isna() | gdf.geometry.is_empty).to_numpy()
    if blank.all():
        return False
    with tempfile.TemporaryDirectory(dir=tmp) as d:
        geojsonl = os.path.join(d, f"{label}.geojsonl")
        wgs84 = gdf.to_crs(4326)
        if blank.any():
            wgs84 = wgs84[~blank]
        del blank
        wgs84.to_file(geojsonl, driver="GeoJSONSeq", engine="pyogrio")
        del wgs84
        pmtiles = os.path.join(d, f"{label}.pmtiles")
        sink_pmtiles.run_tippecanoe(geojsonl, pmtiles, label, what=f"{series_id}/{label}")
        os.remove(geojsonl)
        gcs.upload(pmtiles, pmtiles_object(series_id, label), content_type=config.PMTILES_MIME,
                   cache_control=gcs.CACHE_MUTABLE)
    return True


def _write_manifest(series_id: str, manifest: dict) -> None:
    gcs.put_bytes(json.dumps(manifest).encode(), f"{VECTORS_PREFIX}/{series_id}/_manifest.json",
                  content_type="application/json", cache_control=gcs.CACHE_MUTABLE)


def _record_attempt(series_id: str, record: dict) -> None:
    """Write `_attempt.json`: when this pub was last tried and how it ended. It orders batch runs
    and records failures without touching `_manifest.json`, which ingest publishes from. A
    record that cannot be written is reported, not fatal."""
    record = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **record}
    try:
        gcs.put_bytes(json.dumps(record).encode(), f"{VECTORS_PREFIX}/{series_id}/_attempt.json",
                      content_type="application/json", cache_control=gcs.CACHE_MUTABLE)
    except Exception as e:
        print(f"{series_id}: could not record the attempt: {_error(e)}", file=sys.stderr)


def _extract_and_upload(
    work: str, series_id: str, srcs: list[tuple[str, str | None, str, bool]],
    source_errors: list[dict] | None = None,
) -> dict:
    """Read every source in `srcs`, upload each non-empty layer/table as Parquet to
    `{VECTORS_PREFIX}/{series_id}/{label}.parquet`, and write the per-series manifest. Returns
    that manifest: {"spatial": [labels], "tables": [labels], "layers": [per-layer metadata],
    "empty": [labels], "errors": [...], "warnings": [...]}.

    Spatial layers keep their geometry (GeoParquet via gpio, plus a bbox column) and are tiled to
    `{label}.pmtiles` beside it; a layer whose tiling fails keeps its GeoParquet and gets a
    PMTILES_FAILED warning. Non-spatial GeMS companion tables are read
    WITHOUT geometry via pyogrio and written as plain Parquet. Raw
    schema is preserved verbatim either way — no rename, no type coercion. A layer with 0 rows is
    listed under "empty" and not uploaded. A layer that cannot be read or uploaded, or a spatial
    layer with no CRS, is listed under "errors" and the rest still extract. A CRS-less layer is
    never written: GeoParquet reads a missing CRS as longitude/latitude, so the file would
    mislabel its coordinates. The manifest is written only when at least one layer was
    uploaded, so a failed attempt never replaces a manifest that ingest is publishing from.
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
            os.remove(dst)  # the job's disk is memory; keep only the layer being written
            manifest["spatial" if is_spatial else "tables"].append(label)
            manifest["layers"].append(entry)
        except Exception as e:
            manifest["errors"].append({"label": label, "error": _error(e)})
            print(f"  layer {label} failed: {e}", file=sys.stderr)
            continue
        if is_spatial:
            _tile_into(entry, gdf, series_id, work, manifest)

    if manifest["spatial"] or manifest["tables"]:
        _write_manifest(series_id, manifest)

    return manifest


def _tile_into(entry: dict, gdf, series_id: str, tmp: str | None, manifest: dict) -> bool:
    """Tile one layer and record the result: `pmtiles` and `pmtiles_layer` on its manifest `entry`
    when built, else a warning in `manifest` (PMTILES_FAILED when tiling fails, NO_GEOMETRY when it
    has no geometry). Returns whether the tiles were built."""
    label = entry["label"]
    try:
        built = _tile_layer(gdf, series_id, label, tmp)
    except Exception as e:
        manifest["warnings"].append({"label": label, "code": PMTILES_FAILED,
                                     "warning": f"PMTiles not built: {_error(e)}"})
        print(f"  layer {label}: PMTiles not built: {e}", file=sys.stderr)
        return False
    if not built:
        manifest["warnings"].append({"label": label, "code": NO_GEOMETRY,
                                     "warning": "every geometry is null or empty; no PMTiles built"})
        return False
    entry.update(pmtiles=True, pmtiles_layer=label)
    return True


def _warned(manifest: dict, code: str) -> set[str]:
    return {w.get("label") for w in manifest.get("warnings") or [] if w.get("code") == code}


def tiling_failures(manifest: dict) -> list[str]:
    """Labels of the layers whose tiling failed, from their PMTILES_FAILED warnings."""
    return [w["label"] for w in manifest.get("warnings") or [] if w.get("code") == PMTILES_FAILED]


def missing_tiles(manifest: dict) -> list[str]:
    """Spatial layers still without PMTiles: a PMTILES_FAILED warning, or no `pmtiles` flag and no
    NO_GEOMETRY warning. These are what the backfill builds."""
    failed, blank = _warned(manifest, PMTILES_FAILED), _warned(manifest, NO_GEOMETRY)
    return [e["label"] for e in manifest.get("layers") or [] if e.get("spatial") and (
        e["label"] in failed or not (e.get("pmtiles") or e["label"] in blank))]


def _read_manifest(path: str) -> dict | None:
    """The manifest at `path` when it reads as an object that records per-layer metadata and no
    errors, else None. An unreadable manifest is reported and counts as not done, and so does one
    with no `layers` list, so ordinary resumable runs re-extract it instead of needing --force."""
    try:
        doc = json.loads(gcs.get_bytes(path).decode())
    except Exception as e:
        print(f"  unreadable manifest {path}, re-extracting: {_error(e)}", file=sys.stderr)
        return None
    if not isinstance(doc, dict):
        print(f"  manifest {path} is not an object, re-extracting", file=sys.stderr)
        return None
    return doc if "layers" in doc and not doc.get("errors") else None


def _clean_manifest(path: str) -> bool:
    return _read_manifest(path) is not None


def _series_with(*names: str) -> set[str]:
    """Series ids that have any of the given per-series files, from one listing."""
    pfx = VECTORS_PREFIX.rstrip("/") + "/"
    return {p.removeprefix(pfx).split("/", 1)[0] for p in gcs.list_paths(pfx)
            if p.endswith(tuple(f"/{n}" for n in names))}


def _last_attempt(series_id: str) -> dict:
    """The pub's `_attempt.json`. One that cannot be read is reported and treated as no record,
    so the pub is retried."""
    path = f"{VECTORS_PREFIX}/{series_id}/_attempt.json"
    try:
        doc = json.loads(gcs.get_bytes(path).decode())
    except Exception as e:
        print(f"  unreadable attempt record {path}, retrying: {_error(e)}", file=sys.stderr)
        return {}
    return doc if isinstance(doc, dict) else {}


def clean_manifests() -> dict[str, dict]:
    """{series id: manifest} for each series extracted cleanly: a readable `_manifest.json` that
    lists no errors, from one listing of the vectors prefix. A series with errors, or with layers
    but no manifest, is left out, so every run retries it and reports it again instead of the
    failure going quiet."""
    pfx = VECTORS_PREFIX.rstrip("/") + "/"
    paths = [p for p in gcs.list_paths(pfx) if p.endswith("/_manifest.json")]
    with ThreadPoolExecutor(max_workers=16) as ex:
        docs = list(ex.map(_read_manifest, paths))
    return {p.removeprefix(pfx).split("/", 1)[0]: d for p, d in zip(paths, docs) if d is not None}


def extracted_series() -> set[str]:
    return set(clean_manifests())


def extract_one(series_id: str, dry_run: bool = False, force: bool = False,
                existing: set[str] | None = None, untiled: list[str] | None = None) -> str:
    """Extract and upload all vector layers and non-spatial companion tables for a given
    series_id. Each layer whose tiling failed is appended to `untiled` as `SID/label`.

    Returns: 'ok', 'skip', 'unsupported' (only unreadable containers such as .mdb), or 'fail:*'.
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

    # Recorded before the download, so a run killed mid-pub (out of memory, job timeout) still
    # counts this pub as tried and orders it after the pubs never tried.
    _record_attempt(series_id, {"result": "started"})
    work = tempfile.mkdtemp(prefix=f"v_{series_id.replace('/', '_')}_")
    try:
        zp = os.path.join(work, "gis.zip")
        harvest.download(harvest.encode_url(gis_url), zp)
        remaining = MAX_UNZIPPED_BYTES - _extract_zip(zp, work, MAX_UNZIPPED_BYTES)
        nested_errors, nested_dirs = _unzip_nested(work, remaining)
        srcs, source_errors = _sources(work, nested_dirs)
        source_errors = nested_errors + source_errors
        if not srcs and not source_errors:
            print(f"{series_id}: SKIP (no shapefiles, GDB layers, or tables found)")
            _record_attempt(series_id, {"result": "skip"})
            return "skip"

        manifest = _extract_and_upload(work, series_id, srcs, source_errors)
        failed_tiles = tiling_failures(manifest)
        if failed_tiles:
            print(f"{series_id}: {len(failed_tiles)} layers have no PMTiles (tiling failed): "
                  f"{', '.join(failed_tiles)}", file=sys.stderr)
            if untiled is not None:
                untiled += [f"{series_id}/{lb}" for lb in failed_tiles]
        n_spatial, n_tables = len(manifest["spatial"]), len(manifest["tables"])
        errors = manifest["errors"]
        if errors:
            unreadable = ", ".join(e.get("label") or e["source"] for e in errors)
            # Only unreadable containers (.mdb, .mpk): a known gap, re-reported by later runs
            # without re-downloading, rather than a failure.
            res = ("unsupported" if all(e["error"].startswith("unsupported format") for e in errors)
                   else "fail:layers")
            print(f"{series_id}: {'UNSUPPORTED' if res == 'unsupported' else 'FAIL'} "
                  f"({n_spatial} vector layers + {n_tables} companion tables uploaded, "
                  f"{len(errors)} failed: {unreadable})", file=sys.stderr)
            _record_attempt(series_id, {"result": res, "errors": errors})
            return res
        if n_spatial + n_tables == 0:
            print(f"{series_id}: SKIP (every layer is empty)")
            _record_attempt(series_id, {"result": "skip"})
            return "skip"

        print(f"{series_id}: OK ({n_spatial} vector layers + {n_tables} companion tables "
              f"uploaded to {VECTORS_PREFIX}/{series_id}/)")
        _record_attempt(series_id, {"result": "ok"})
        return "ok"
    except Exception as e:
        print(f"{series_id}: FAIL {e}", file=sys.stderr)
        _record_attempt(series_id, {"result": f"fail:{type(e).__name__}",
                                    "errors": [{"source": "gis zip", "error": _error(e)}]})
        return f"fail:{type(e).__name__}"
    finally:
        shutil.rmtree(work, ignore_errors=True)



def _backfill_series(series_id: str, force: bool = False) -> tuple[int, int, list[str]]:
    """Build the PMTiles a series' manifest does not record yet (including layers with a
    PMTILES_FAILED warning), from the GeoParquet already on GCS, and record them in the manifest.
    `force` rebuilds layers that have them. Returns (layers built, layers skipped, an error per
    layer that failed). A layer's tiling warnings are replaced by this attempt's outcome."""
    import geopandas as gpd

    path = f"{VECTORS_PREFIX}/{series_id}/_manifest.json"
    raw, version = gcs.get_bytes_versioned(path)
    manifest = json.loads(raw.decode())
    if not isinstance(manifest, dict) or not isinstance(manifest.get("layers"), list):
        raise ValueError(f"{path} records no per-layer metadata; re-extract with --force")
    manifest.setdefault("warnings", [])
    before = json.dumps(manifest)
    missing = set(missing_tiles(manifest))
    spatial = [e for e in manifest["layers"] if e.get("spatial")]
    todo = [e for e in spatial if force or e["label"] in missing]
    built, errors = 0, []
    # The same per-series work dir extraction uses; it holds one layer's files at a time.
    with tempfile.TemporaryDirectory(prefix=f"v_{series_id.replace('/', '_')}_") as work:
        for entry in todo:
            label = entry["label"]
            local = os.path.join(work, f"{label}.parquet")
            try:
                gcs.download(f"{VECTORS_PREFIX}/{series_id}/{label}.parquet", local)
                gdf = gpd.read_parquet(local)
            except Exception as e:
                errors.append(f"{series_id}/{label}: {_error(e)}")
                print(f"  {series_id}/{label}: GeoParquet unreadable: {e}", file=sys.stderr)
                continue
            finally:
                if os.path.exists(local):
                    os.remove(local)
            manifest["warnings"] = [w for w in manifest["warnings"] if not (
                w.get("label") == label and w.get("code") in (PMTILES_FAILED, NO_GEOMETRY))]
            if _tile_into(entry, gdf, series_id, work, manifest):
                built += 1
            del gdf
            errors += [f"{series_id}/{label}: {w['warning']}" for w in manifest["warnings"]
                       if w.get("label") == label and w.get("code") == PMTILES_FAILED]
    if json.dumps(manifest) != before:
        gcs.put_bytes_if_unchanged(json.dumps(manifest).encode(), path, version,
                                   content_type="application/json", cache_control=gcs.CACHE_MUTABLE)
    return built, len(spatial) - len(todo), errors


def backfill_pmtiles(series_ids: list[str], force: bool = False, limit: int | None = None) -> int:
    """Build missing PMTiles for already-extracted layers without re-downloading any zip. No
    `series_ids` means every series with a clean manifest. Prints a summary; returns 1 when any
    layer or series failed."""
    sids = ([identity.Pub.parse(s).series_id for s in series_ids] if series_ids
            else sorted(extracted_series()))
    if limit:
        sids = sids[:limit]
    built = skipped = 0
    errors: list[str] = []
    for sid in sids:
        try:
            b, sk, errs = _backfill_series(sid, force=force)
        except Exception as e:
            errors.append(f"{sid}: {_error(e)}")
            print(f"{sid}: FAIL {_error(e)}", file=sys.stderr)
            continue
        built, skipped, errors = built + b, skipped + sk, errors + errs
        print(f"{sid}: {b} PMTiles built, {sk} skipped"
              + (f", {len(errs)} failed" if errs else ""))
    print(f"PMTiles backfill over {len(sids)} series: {built} built, {skipped} skipped, "
          f"{len(errors)} failed")
    if errors:
        print("Failed:\n  " + "\n  ".join(errors), file=sys.stderr)
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Extract GIS publication vector layers to GCS")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("series_id", nargs="*", default=[], help="Series ID(s) to extract")
    g.add_argument("--all", action="store_true", help="Extract all series IDs from metadata")
    ap.add_argument("--limit", type=int, default=None, help="Limit number of publications to extract")
    ap.add_argument("--dry-run", action="store_true", help="Dry run (check URLs and schema only)")
    ap.add_argument("--force", action="store_true", help="Force extraction even if artifacts exist")
    ap.add_argument("--pmtiles-backfill", action="store_true",
                    help="Build missing PMTiles from extracted GeoParquet (the given series, or every "
                         "cleanly extracted one); --force rebuilds existing ones")
    args = ap.parse_args()

    if args.pmtiles_backfill:
        if args.all or args.dry_run:
            ap.error("--pmtiles-backfill takes series ids or none, not --all or --dry-run")
        return backfill_pmtiles(args.series_id, force=args.force, limit=args.limit)
    if not args.all and not args.series_id:
        ap.error("give series ids or --all")

    sids = []
    existing = None
    standing: list[str] = []
    if args.all:
        print(f"Loading publications from source: {source.source_name()}")
        all_sids = [s for p in source.read_pubs() if (s := (p.get("series_id") or "").strip())]
        # A batch run only visits pubs whose attachments list a GIS zip, and checks what is already
        # extracted with one listing, so a weekly run costs about nothing when nothing is new.
        sids = [s for s in all_sids if harvest._get_attached_zips(s)[1]]
        manifests = {} if args.force else clean_manifests()
        existing = None if args.force else set(manifests)
        # Clean pubs are skipped below, so their manifests are the only record of their untiled
        # layers. A forced run re-tiles every pub and reports them itself.
        standing = [f"{s}/{lb}" for s in sorted(set(sids) & set(manifests))
                    for lb in missing_tiles(manifests[s])]
        del manifests
        unsupported: list[str] = []
        nothing: list[str] = []
        if existing is not None:
            tried = _series_with("_attempt.json", "_manifest.json")
            attempted = _series_with("_attempt.json")
            # A pub whose last attempt failed only on unreadable containers, or finished cleanly
            # with nothing to extract, is listed, not re-downloaded; --force retries it (for
            # example after the image gains a driver).
            for s in [s for s in sids if s in attempted and s not in existing]:
                last = _last_attempt(s)
                if last.get("result") == "unsupported":
                    kinds = ", ".join(sorted({e["source"] for e in last.get("errors", [])}))
                    unsupported.append(f"{s} ({kinds})")
                    sids.remove(s)
                elif last.get("result") == "skip":
                    nothing.append(s)
                    sids.remove(s)
            # Pubs never tried go first, retries of earlier failures and upgrades last, so a pub
            # that fails slowly every run cannot spend the job's timeout before new pubs are reached.
            sids = sorted(sids, key=lambda s: s in tried)
        print(f"Found {len(all_sids)} publications, "
              f"{len(sids) + len(unsupported) + len(nothing)} with a GIS zip, "
              f"{len(set(sids) & (existing or set()))} already extracted")
        if nothing:
            print(f"{len(nothing)} had no layers to extract last time, not retried (--force to "
                  f"retry): {', '.join(nothing)}")
        if unsupported:
            print(f"{len(unsupported)} contain formats this image cannot read (any readable "
                  f"layers were extracted), not retried (--force to retry): {', '.join(unsupported)}")
    else:
        sids = args.series_id

    if args.limit:
        sids = sids[:args.limit]

    failed: list[str] = []
    untiled: list[str] = []
    for sid in sids:
        res = extract_one(sid, dry_run=args.dry_run, force=args.force, existing=existing,
                          untiled=untiled)
        if res.startswith("fail"):
            failed.append(f"{sid} ({res})")
    if untiled:
        print(f"{len(untiled)} layers published without PMTiles (tiling failed; retry with "
              f"--pmtiles-backfill): {', '.join(untiled)}", file=sys.stderr)
    if standing:
        print(f"{len(standing)} layers still without PMTiles, run --pmtiles-backfill: "
              f"{', '.join(standing)}", file=sys.stderr)
    if failed:
        print(f"{len(failed)} of {len(sids)} publications failed: {', '.join(failed)}",
              file=sys.stderr)
    return 1 if failed or untiled or standing else 0


if __name__ == "__main__":
    sys.exit(main())
