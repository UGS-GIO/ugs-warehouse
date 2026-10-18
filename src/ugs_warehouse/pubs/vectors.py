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
display, by DuckDB exporting its GeoParquet to GeoJSONSeq for tippecanoe; its manifest entry records
`pmtiles` and `pmtiles_layer`. A layer whose tiling fails
keeps its GeoParquet and gets a `pmtiles_failed` warning. Each tiled layer also gets `<label>.style.json`
beside its tiles (`layer_style.py`, the viewer's default look), recorded as `style` on its entry.

Every data attachment is opened, not only the zip labelled GIS: zips of any label, and bare or
zipped `.xlsx`/`.xls`/`.csv` files (`sheets.py`), whose sheets become tables and, with coordinates in
Utah, point layers. Each layer records its attachment as `source_file`, and the manifest records
`extract_version`, so a pub extracted at an older coverage is extracted again.

The manifest also lists the relationships between a bundle's layers and tables (`relations.py`):
declared geodatabase relationship classes, and joins inferred within one source dataset. Each
layer entry records its `source` dataset and its `kind` (`layer`, `table` or `supporting`).

After extracting, a run self-heals the cleanly extracted pubs it covers (`self_heal`): it tiles
layers that have no PMTiles, records relationships where the manifest has none or predates
`relations.RELATIONS_VERSION`, writes the layer styles where they are missing or predate
`layer_style.STYLE_VERSION`, and builds the pub's `unit_polys` product where the manifest predates
`unit_polys.UNIT_POLYS_VERSION`. An `--all` run then builds the identify layers under the mosaics
(`identify.py`) for the tiers whose members' unit polygons changed. In an `--all` run, extraction
and self-heal share one time budget (`VECTORS_TIME_BUDGET_S`); what it leaves waits for the next run.

A pub with any unreadable layer returns `fail:layers`, so a missing layer never looks like a pub
that simply has none. The batch run exits non-zero for it, unless every error is bad source data
(`DATA_ERRORS`) and identical to the pub's last attempt: that pub is listed as a known data problem
and retried every run.

    python -m ugs_warehouse.pubs.vectors M-299DM
    python -m ugs_warehouse.pubs.vectors --all
"""
from __future__ import annotations

import argparse
import hashlib
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
from dataclasses import dataclass, field
from typing import NamedTuple
from urllib.parse import unquote, urlsplit

from ..core import config, gcs, jobs, stac
from ..vector import sink_pmtiles, transform
from . import (
    geoparquet,
    harvest,
    identify,
    identity,
    layer_style,
    relations,
    sheets,
    source,
    unit_polys,
)

VECTORS_PREFIX = os.environ.get("GEOLMAP_VECTORS_PREFIX", "geolmap/vectors")
PARQUET_MIME = config.PARQUET_MIME
# Warning code for a layer whose GeoParquet published but whose tiles did not. A warning, not an
# error, so the series stays clean and the batch run does not download its zip again.
PMTILES_FAILED = "pmtiles_failed"
NON_FINITE_GEOMETRY = "non_finite_geometry"
# Warning code for a layer with no geometry to tile. Not a failure: it never gets PMTiles.
NO_GEOMETRY = "no_geometry"
# Warning code for a layer whose style file could not be written. A warning, not an error, so the
# series stays clean.
STYLE_FAILED = "style_failed"
# Warning code for a shapefile whose attribute text was not UTF-8 and was read as cp1252.
ENCODING_FALLBACK = "encoding_fallback"
# Warning codes for the all-layers GeoPackage: a pub whose GeoParquet is too large to package (no
# GeoPackage is published), and one whose GeoPackage could not be built (retried by the next heal).
GEOPACKAGE_SKIPPED = "geopackage_skipped"
GEOPACKAGE_FAILED = "geopackage_failed"
# Warning code for a rebuild that failed while an earlier GeoPackage is still published.
GEOPACKAGE_STALE = "geopackage_stale"
# Warning code for a table left out of the GeoPackage because it has more columns than SQLite allows.
GEOPACKAGE_TABLE_SKIPPED = "geopackage_table_skipped"
# SQLite's default column limit per table; the GeoPackage's feature id and geometry count against it.
GPKG_MAX_COLUMNS = 2000
# The feature id column the GeoPackage layers are created with. GDAL's default, `fid`, collides with
# a source column of that name.
GPKG_FID_COLUMN = "gpkg_fid"
# Bump when the GeoPackage layout changes; a manifest below it is packaged again by the heal.
GEOPACKAGE_VERSION = 1
GEOPACKAGE_MIME = "application/geopackage+sqlite3"
# A pub whose layers' GeoParquet files total more than this gets no GeoPackage: it is built on the
# job's RAM-backed disk, and a GeoPackage runs about 2.5 times the size of its GeoParquet.
MAX_GPKG_BYTES = 2**30
# Rows read from a GeoParquet and appended to the GeoPackage at a time.
GPKG_BATCH_ROWS = 20_000
# The GeoPackage table that lists the pub's relationships (the Related Tables Extension is not
# written: the image's GDAL bindings are not installed, and pyogrio has no relationship API).
GPKG_RELATIONSHIPS = "ugs_relationships"
# The coverage of an extraction: 1 opened only the zip labelled GIS and read shapefiles and
# geodatabases; 2 opens every data zip and reads spreadsheets and CSV. A clean manifest below this
# is extracted again by the next regular run.
EXTRACT_VERSION = 2


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


def _junk_entry(name: str) -> bool:
    """True for the resource-fork entries macOS adds to a zip (`__MACOSX/...`, `._name`) and for
    Excel lock files (`~$name.xlsx`); none is data, and the `._` copies of a shapefile are binary
    junk with the real name."""
    parts = name.replace("\\", "/").split("/")
    return parts[0] == "__MACOSX" or any(p.startswith(("._", "~$")) for p in parts)


def _extract_zip(path: str, dest: str, remaining: int) -> int:
    """Extract `path` into `dest` without its junk entries (`_junk_entry`), delete the zip, and
    return the bytes it unzipped to. Raises without extracting when that would exceed
    `remaining`."""
    with zipfile.ZipFile(path) as z:
        members = [i for i in z.infolist() if not _junk_entry(i.filename)]
        size = sum(i.file_size for i in members)
        if size > remaining:
            raise ValueError(f"unzips to {size / 2**30:.1f} GiB, over the "
                             f"{MAX_UNZIPPED_BYTES / 2**30:g} GiB limit for the job's in-memory disk")
        z.extractall(dest, members=members)
    os.remove(path)
    return size


def _tree_bytes(root: str) -> int:
    """Bytes of every file under `root`: what the attachments extracted so far occupy."""
    return sum(os.path.getsize(os.path.join(d, n)) for d, _, names in os.walk(root) for n in names)


def _nested_dest(path: str) -> str:
    """A folder for an inner zip's contents that does not exist yet, so nothing merges into an
    existing folder. `X.gdb.zip` holding bare geodatabase files extracts to `X.gdb` so it reads as
    a geodatabase; one holding an `X.gdb/` folder goes to `X.gdb.zip_unzipped/X.gdb`."""
    stem = os.path.splitext(path)[0]
    if stem.lower().endswith(".gdb") and not os.path.exists(stem):
        with zipfile.ZipFile(path) as z:
            if not any("/" in n.rstrip("/") for n in z.namelist() if not _junk_entry(n)):
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


def _sources(work: str, nested_dirs: list[str] | tuple = (), used: set[str] | None = None
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
    path inside the bundle as a prefix, or a numeric suffix at the bundle root. Spreadsheets and CSV
    files come after the shapefiles and geodatabases, so they never rename a published layer: each
    sheet of a workbook is a table labelled `<workbook>__<sheet>`, and a CSV is `<file>`. `used`
    holds the labels of the publication's earlier attachments and gains this bundle's.
    """
    import pyogrio

    out: list[tuple[str, str | None, str, bool]] = []
    errors: list[dict] = []
    used = set() if used is None else used

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

    for path, ext in entries:
        name = os.path.basename(path)
        if ext not in sheets.SHEET_EXTS or not os.path.isfile(path):
            continue
        stem = _safe(name[:-len(ext)])
        try:
            for sheet in sheets.sheet_names(path):
                label = stem if sheet is None else f"{stem}__{_safe(sheet)}"
                out.append((path, sheet, unique(label, path), False))
        except Exception as e:
            errors.append({"source": name, "error": _error(e)})
            print(f"  spreadsheet {name} unreadable: {e}", file=sys.stderr)
    return out, errors


def _schema(source) -> tuple[list[dict], dict | None]:
    """The written file's columns in the STAC table extension's shape ([{name, type}]) and its
    GeoParquet `geo` metadata, if any. `source` is a path or file object. Reading both from the
    file keeps it the single source."""
    import pyarrow.parquet as pq

    return _columns_of(pq.read_schema(source))


def _columns_of(schema) -> tuple[list[dict], dict | None]:
    meta = schema.metadata or {}
    geo = json.loads(meta[b"geo"]) if b"geo" in meta else None
    geom_cols = set((geo or {}).get("columns", {}))
    return [{"name": f.name, "type": "geometry" if f.name in geom_cols else stac.table_type(f.type)}
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


def geopackage_object(series_id: str) -> str:
    return f"{VECTORS_PREFIX}/{series_id}/{series_id}.gpkg"


def style_object(series_id: str, label: str) -> str:
    return f"{VECTORS_PREFIX}/{series_id}/{label}.style.json"


def unit_polys_object(series_id: str) -> str:
    return f"{VECTORS_PREFIX}/{series_id}/unit_polys.parquet"


def unit_polys_pmtiles_object(series_id: str) -> str:
    return f"{VECTORS_PREFIX}/{series_id}/unit_polys.pmtiles"


# DuckDB's memory cap for one tiling export. The export streams, so this bounds one wide row
# group, not the layer.
TILE_DUCKDB_MEMORY = "1GB"

# Warning code for a layer whose tiles lack some attribute columns the GeoParquet has.
TILE_COLUMNS_DROPPED = "tile_columns_dropped"

# Column types the GDAL GeoJSONSeq writer drops (TIME) or writes without the zone (timestamps
# with a time zone); they are cast to ISO strings so the tiles keep the value.
_STRING_CAST_TYPES = {"TIME", "TIME WITH TIME ZONE", "TIMESTAMP WITH TIME ZONE"}


def _ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _literal(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _source_crs(entry: dict, geo: dict, geom_col: str) -> str:
    """The CRS the layer's coordinates are in, for DuckDB's ST_Transform: the manifest's EPSG code
    when it recorded one, else its WKT2, else the GeoParquet's own CRS. Raises when there is none,
    since reprojecting from a guessed CRS would put the tiles in the wrong place."""
    if entry.get("proj_code"):
        return entry["proj_code"]
    if entry.get("proj_wkt2"):
        return entry["proj_wkt2"]
    projjson = (geo.get("columns", {}).get(geom_col) or {}).get("crs")
    if projjson:
        from pyproj import CRS

        return CRS.from_user_input(projjson).to_wkt()
    raise ValueError("no CRS recorded for the layer; cannot reproject it to EPSG:4326")


def _write_wgs84(parquet: str, entry: dict, geojsonl: str, dropped: list[str] | None = None) -> bool:
    """Write the layer's GeoParquet as GeoJSONSeq in lon/lat, through DuckDB (the export streams, so
    the layer is never in memory). Rows with no geometry or an empty one are left out before
    reprojecting. `always_xy` keeps coordinates in lon/lat order whatever the source CRS's axis order.
    Returns False, writing nothing, when no row has a geometry. Columns the first feature lacks
    are appended to `dropped`, so the caller can warn instead of losing them silently."""
    import duckdb

    columns, geo = _schema(parquet)
    geo = geo or {}
    col = geo.get("primary_column")
    if not col:
        raise ValueError("the GeoParquet has no geometry column in its metadata")
    # The bbox covering column is an index, not an attribute, so it does not go into the tiles.
    covering = ((geo.get("columns", {}).get(col) or {}).get("covering") or {}).get("bbox") or {}
    drop = {col, *(v[0] for v in covering.values() if isinstance(v, list) and v)}
    kept = [c["name"] for c in columns if c["name"] not in drop]
    crs = _source_crs(entry, geo, col)
    geom = _ident(col)
    wgs84 = geom if crs.upper() == "EPSG:4326" else (
        f"ST_Transform({geom}, {_literal(crs)}, 'EPSG:4326', always_xy := true)")
    con = duckdb.connect()
    try:
        transform.setup(con)
        con.execute(f"SET max_memory={_literal(TILE_DUCKDB_MEMORY)}")
        con.execute("SET TimeZone = 'UTC'")  # zoned timestamps are written in UTC, not the host's zone
        types = {r[0]: r[1] for r in con.execute(
            f"DESCRIBE SELECT * FROM read_parquet({_literal(parquet)})").fetchall()}
        props = [f"CAST({_ident(c)} AS VARCHAR) AS {_ident(c)}" if types.get(c) in _STRING_CAST_TYPES
                 else _ident(c) for c in kept]
        con.execute(
            f"CREATE TEMP VIEW tiles AS SELECT {', '.join([*props, f'{wgs84} AS __geom'])} "
            f"FROM read_parquet({_literal(parquet)}) "
            f"WHERE {geom} IS NOT NULL AND NOT ST_IsEmpty({geom})")
        if con.execute("SELECT 1 FROM tiles LIMIT 1").fetchone() is None:
            return False
        sink_pmtiles.write_geojsonl(con, "tiles", geojsonl)
        if dropped is not None:
            dropped += _missing_attributes(geojsonl, kept, types)
    finally:
        con.close()
    return True


def _missing_attributes(geojsonl: str, columns: list[str], types: dict[str, str]) -> list[str]:
    """The `columns` the first feature of the export has no property for. A struct is written as
    `name.field` properties, so struct and map columns are matched by prefix."""
    with open(geojsonl) as fh:
        props = (json.loads(fh.readline()).get("properties") or {}).keys()
    nested = ("STRUCT", "MAP", "UNION")
    return [c for c in columns if c not in props and not (
        (types.get(c) or "").startswith(nested) and any(k.startswith(f"{c}.") for k in props))]


def _tile_layer(parquet: str, entry: dict, series_id: str, tmp: str | None = None,
                dropped: list[str] | None = None) -> bool:
    """Tile the layer whose GeoParquet is the local file `parquet` as `<label>.pmtiles` beside its
    published GeoParquet: reprojected to EPSG:4326 (tippecanoe reads lon/lat), one tile layer named
    for the label, built with the serving topics' tippecanoe flags. `entry` is its manifest entry
    (label, CRS). Returns False, writing nothing, when no row has a geometry. Attribute columns the
    export lost are appended to `dropped`. Temp files go under `tmp` and are deleted before
    returning, since the job's disk is memory."""
    label = entry["label"]
    with tempfile.TemporaryDirectory(dir=tmp) as d:
        geojsonl = os.path.join(d, f"{label}.geojsonl")
        if not _write_wgs84(parquet, entry, geojsonl, dropped):
            return False
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
    record = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "extract_version": EXTRACT_VERSION, **record}
    try:
        gcs.put_bytes(json.dumps(record).encode(), f"{VECTORS_PREFIX}/{series_id}/_attempt.json",
                      content_type="application/json", cache_control=gcs.CACHE_MUTABLE)
    except Exception as e:
        print(f"{series_id}: could not record the attempt: {_error(e)}", file=sys.stderr)


@dataclass(frozen=True)
class Origin:
    """The attachment a layer came from: the folder it was extracted into (paths inside it are
    the layer's `source_file.path`), the Collection asset key of the attachment and its title."""
    root: str
    key: str | None
    title: str


def _is_sheet(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in sheets.SHEET_EXTS


def _read_spatial(path: str, layer: str | None, label: str, manifest: dict):
    """A spatial layer as a GeoDataFrame. A shapefile whose attribute text is not valid UTF-8 is
    read again as cp1252 and gets an ENCODING_FALLBACK warning; any other read error propagates."""
    import geopandas as gpd

    try:
        return gpd.read_file(path, layer=layer, engine="pyogrio")
    except UnicodeDecodeError:
        gdf = gpd.read_file(path, layer=layer, engine="pyogrio", encoding="cp1252")
    manifest["warnings"].append({"label": label, "code": ENCODING_FALLBACK,
                                 "warning": "attribute text is not valid UTF-8; read as cp1252"})
    return gdf


def _dedupe_columns(frame, label: str, manifest: dict) -> None:
    """Rename `frame`'s columns that repeat another ignoring case (`Name`, `NAME`) to `_2`, `_3`,
    and warn with the renames. The geometry column keeps its name."""
    geom = getattr(frame, "_geometry_column_name", None)
    names = [str(c) for c in frame.columns]
    data = [n for n in names if n != geom]
    new, renames = sheets.dedupe_columns(data, reserved=(geom,) if geom else ())
    if not renames:
        return
    fresh = iter(new)
    frame.columns = [n if n == geom else next(fresh) for n in names]
    manifest["warnings"].append({"label": label, "warning": sheets.rename_warning(renames)})


def _extract_and_upload(
    work: str, series_id: str, srcs: list[tuple[str, str | None, str, bool]],
    source_errors: list[dict] | None = None, origins: dict[str, Origin] | None = None,
) -> dict:
    """Read every source in `srcs`, upload each non-empty layer/table as Parquet to
    `{VECTORS_PREFIX}/{series_id}/{label}.parquet`, and write the per-series manifest. Returns
    that manifest: {"spatial": [labels], "tables": [labels], "layers": [per-layer metadata],
    "relationships": [...], "empty": [labels], "errors": [...], "warnings": [...]}.

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

    A spreadsheet or CSV source (`sheets.py`) is a table per sheet that has a header row; one with
    latitude/longitude columns in Utah also yields `<label>_points`, a point layer. A sheet with
    nothing to extract is a warning, not an error. `origins` maps a label to the attachment it came
    from, recorded on its entry as `source_file` {key, title, path}.
    """
    import pyogrio

    manifest: dict = {"spatial": [], "tables": [], "layers": [], "empty": [],
                      "errors": list(source_errors or []), "warnings": [],
                      "extract_version": EXTRACT_VERSION}
    origins = dict(origins or {})
    profiles: list[relations.Layer] = []
    pending = list(srcs)
    point_frames: dict[str, object] = {}
    books: dict[str, sheets.Workbook] = {}  # each workbook is opened once, for all its sheets
    try:
        while pending:
            path, layer, label, is_spatial = pending.pop(0)
            if label.casefold() == GPKG_RELATIONSHIPS:
                manifest["errors"].append({
                    "label": label,
                    "error": f"label is reserved for the GeoPackage's {GPKG_RELATIONSHIPS} table"})
                continue
            dst = os.path.join(work, f"{label}.parquet")
            gdf = df = None  # the previous layer is released before this one is read
            origin = origins.get(label)
            root = origin.root if origin else work
            try:
                if label in point_frames:
                    gdf = point_frames.pop(label)
                    rows = len(gdf)
                    geoparquet.write(gdf, dst)
                elif is_spatial:
                    gdf = _read_spatial(path, layer, label, manifest)
                    _dedupe_columns(gdf, label, manifest)
                    rows = len(gdf)
                    if rows and gdf.crs is None:
                        raise ValueError("no CRS (missing or unreadable .prj); not written, since "
                                         "GeoParquet would read its coordinates as longitude/latitude")
                    blanked = geoparquet.blank_non_finite(gdf)
                    if blanked:
                        text = (f"{blanked} geometries with a non-finite coordinate were "
                                "written as null")
                        manifest["warnings"].append({"label": label, "code": NON_FINITE_GEOMETRY,
                                                     "warning": text})
                        print(f"  {label}: {text}", file=sys.stderr)
                    if rows:
                        geoparquet.write(gdf, dst)
                elif _is_sheet(path):
                    if layer is not None and path not in books:
                        books[path] = sheets.Workbook(path)
                    sheet = sheets.read(path, layer, books.get(path))
                    manifest["warnings"] += [{"label": label, "warning": w} for w in sheet.warnings]
                    for w in sheet.warnings:
                        print(f"  {label}: {w}", file=sys.stderr)
                    if sheet.frame is None:
                        continue
                    df = sheet.frame
                    rows = len(df)
                    df.to_parquet(dst)
                else:
                    df = pyogrio.read_dataframe(path, layer=layer, read_geometry=False)
                    _dedupe_columns(df, label, manifest)
                    rows = len(df)
                    if rows:
                        df.to_parquet(dst)
                if not rows:
                    manifest["empty"].append(label)
                    continue

                columns, geo = _schema(dst)
                entry = {"label": label, "spatial": is_spatial, "rows": rows, "columns": columns,
                         "source": _dataset_name(root, series_id, path, layer, label)}
                if origin:
                    entry["source_file"] = {
                        **({"key": origin.key} if origin.key else {}), "title": origin.title,
                        "path": _path_in(root, path)}
                if is_spatial:
                    entry.update(_spatial_meta(gdf, geo, label, manifest["warnings"]))
                gcs_path = f"{VECTORS_PREFIX}/{series_id}/{label}.parquet"
                meta = gcs.upload(dst, gcs_path, content_type=PARQUET_MIME,
                                  cache_control=gcs.CACHE_MUTABLE)
                entry.update(bytes=meta.size, checksum=meta.checksum)
                manifest["spatial" if is_spatial else "tables"].append(label)
                manifest["layers"].append(entry)
                if df is not None and _is_sheet(path):
                    _queue_points(df, label, path, layer, origin, pending, point_frames, origins,
                                  manifest)
            except Exception as e:
                manifest["errors"].append({"label": label, "error": _error(e)})
                print(f"  layer {label} failed: {e}", file=sys.stderr)
                if os.path.exists(dst):
                    os.remove(dst)
                continue
            _profile_into(profiles, entry, gdf if is_spatial else df,
                          layer or os.path.basename(path)[:-4], manifest)
            # The frame is released before tiling, which reads the file just written; the job's disk
            # is memory, so the file goes as soon as the layer is done.
            gdf = df = None
            if is_spatial:
                _tile_into(entry, dst, series_id, work, manifest)
            os.remove(dst)
    finally:
        for book in books.values():
            book.close()

    if manifest["spatial"] or manifest["tables"]:
        declared = _declared_relationships(srcs, {e["label"] for e in manifest["layers"]}, manifest)
        _set_relationships(manifest, relations.merge(declared, relations.infer(profiles)))
        for err in _style_into(series_id, manifest):
            print(f"  {err}", file=sys.stderr)
        _write_manifest(series_id, manifest)

    return manifest


def _path_in(root: str, path: str) -> str:
    """`path` relative to `root` with `/` separators: where a layer's file sits inside its
    attachment (just the file name for a bare attachment)."""
    return os.path.relpath(path, root).replace(os.sep, "/")


def _queue_points(df, label: str, path: str, layer: str | None, origin: Origin | None,
                  pending: list, point_frames: dict, origins: dict[str, Origin],
                  manifest: dict) -> None:
    """If the sheet table `df` has coordinates (`sheets.points`), queue a `<label>_points` layer
    next, with the frame kept in `point_frames` rather than read again. What the detection says
    about the columns or the rows left out is recorded as warnings on the table's label."""
    notes: list[str] = []
    try:
        pts = sheets.points(df, notes)
    except Exception as e:
        manifest["errors"].append({"label": f"{label}_points", "error": _error(e)})
        print(f"  layer {label}_points failed: {e}", file=sys.stderr)
        return
    for note in notes:
        manifest["warnings"].append({"label": label, "warning": note})
        print(f"  {label}: {note}", file=sys.stderr)
    if pts is None:
        return
    points_label = f"{label}_points"
    point_frames[points_label] = pts
    if origin:
        origins[points_label] = origin
    pending.insert(0, (path, layer, points_label, True))


def _dataset_name(work: str, series_id: str, path: str, layer: str | None, label: str) -> str:
    """The source dataset a layer came from, which is also its inference scope: for a geodatabase
    layer its label's prefix (the stem, after the folder when the name collided), so the self-heal
    reads the same scope from the label; for a shapefile its folder inside the bundle (the series
    id for the bundle's root), which a collided label also carries as its prefix."""
    if layer is not None:
        return relations.source_of_label(label, series_id)
    folder, root = os.path.dirname(os.path.abspath(path)), os.path.abspath(work)
    if folder == root:
        return series_id
    rel = os.path.relpath(folder, root) if folder.startswith(root + os.sep) else os.path.basename(folder)
    return _safe(rel.replace(os.sep, "__"))


def _profile_into(profiles: list, entry: dict, frame, name: str, manifest: dict) -> None:
    """Keep what relationship inference needs of one layer read in memory, scoped by its `source`.
    A spatial layer is never an inferred target, so it keeps no key sets. A layer that cannot be
    profiled at all is an error, and extraction goes on."""
    try:
        columns, failed = relations.profile_frame(frame, keys=not entry["spatial"])
        profiles.append(relations.Layer(entry["label"], name, entry["source"], columns,
                                        entry["spatial"]))
    except Exception as e:
        manifest["errors"].append({"label": entry["label"],
                                   "error": f"not profiled for relationships: {_error(e)}"})
        print(f"  layer {entry['label']}: not profiled for relationships: {e}", file=sys.stderr)
        return
    _warn_unprofiled(manifest, entry["label"], failed)


def _warn_unprofiled(manifest: dict, label: str, failed: list[str]) -> None:
    """One warning for a layer's columns that could not be profiled; its other columns still are."""
    if failed:
        manifest.setdefault("warnings", []).append({
            "label": label, "code": relations.PROFILE_FAILED,
            "warning": f"columns left out of relationship inference: {'; '.join(failed)}"})
        print(f"  layer {label}: columns not profiled for relationships: {failed}", file=sys.stderr)


def _declared_relationships(srcs: list[tuple[str, str | None, str, bool]], uploaded: set[str],
                            manifest: dict) -> list[dict]:
    """The relationship classes each geodatabase in `srcs` declares, between uploaded labels. A
    geodatabase whose relationships cannot be read, and each one not published, gets a warning."""
    out: list[dict] = []
    for gdb in sorted({path for path, layer, _, _ in srcs
                       if layer is not None and not _is_sheet(path)}):
        name = os.path.basename(gdb)
        rels, why = relations.read_declared(gdb)
        if rels is None:
            manifest["warnings"].append({"source": name, "code": relations.RELATIONSHIPS_NOT_READ,
                                         "warning": f"declared relationships not read: {why}"})
            print(f"  {name}: declared relationships not read: {why}", file=sys.stderr)
            continue
        labels = {layer: label for path, layer, label, _ in srcs
                  if path == gdb and layer is not None and label in uploaded}
        for rel in rels:
            oriented, why = relations.orient_declared(rel, labels)
            if oriented:
                out.append(oriented)
            else:
                manifest["warnings"].append({"source": name, "code": relations.RELATIONSHIP_SKIPPED,
                                             "warning": f"relationship {rel.get('name')!r} {why}"})
    return out


def _set_relationships(manifest: dict, rels: list[dict]) -> None:
    """Record `rels`, the inference rules' version and each layer's resulting `kind`."""
    manifest["relationships"] = rels
    manifest["relations_version"] = relations.RELATIONS_VERSION
    for entry in manifest["layers"]:
        entry["kind"] = relations.layer_kind(bool(entry.get("spatial")), entry["label"], rels)


def _tile_into(entry: dict, parquet: str, series_id: str, tmp: str | None, manifest: dict) -> bool:
    """Tile one layer from its local GeoParquet `parquet` and record the result: `pmtiles` and
    `pmtiles_layer` on its manifest `entry` when built, else a warning in `manifest`
    (PMTILES_FAILED when tiling fails, NO_GEOMETRY when it has no geometry). A built layer whose
    tiles lack attribute columns also gets a TILE_COLUMNS_DROPPED warning naming them. Returns
    whether the tiles were built."""
    label = entry["label"]
    dropped: list[str] = []
    try:
        built = _tile_layer(parquet, entry, series_id, tmp, dropped)
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
    if dropped:
        manifest["warnings"].append({"label": label, "code": TILE_COLUMNS_DROPPED,
                                     "warning": f"columns missing from the tiles: {', '.join(dropped)}"})
        print(f"  layer {label}: columns missing from the tiles: {dropped}", file=sys.stderr)
    return True


def _style_into(series_id: str, manifest: dict) -> list[str]:
    """Write `<label>.style.json` for each spatial layer of `manifest` that has PMTiles and record
    `style` (the file name) on its entry. Returns an error per layer whose style could not be
    written; that layer keeps no `style` and gets a STYLE_FAILED warning, and `style_version` is left
    unset so the next heal tries again. Earlier style warnings are replaced."""
    manifest["warnings"] = [w for w in manifest.get("warnings") or [] if w.get("code") != STYLE_FAILED]
    errors: list[str] = []
    for entry in manifest["layers"]:
        label = entry["label"]
        if not (entry.get("spatial") and entry.get("pmtiles")):
            continue
        entry.pop("style", None)
        try:
            gcs.put_bytes(json.dumps(layer_style.default_style(label)).encode(),
                          style_object(series_id, label), content_type="application/json",
                          cache_control=gcs.CACHE_MUTABLE)
        except Exception as e:
            errors.append(f"{series_id}/{label}: style not written: {_error(e)}")
            manifest["warnings"].append({"label": label, "code": STYLE_FAILED,
                                         "warning": f"style not written: {_error(e)}"})
            continue
        entry["style"] = f"{label}.style.json"
    if not errors:
        manifest["style_version"] = layer_style.STYLE_VERSION
    return errors


def _warned(manifest: dict, code: str) -> set[str]:
    return {w.get("label") for w in manifest.get("warnings") or [] if w.get("code") == code}


def tiling_failures(manifest: dict) -> list[str]:
    """Labels of the layers whose tiling failed, from their PMTILES_FAILED warnings."""
    return [w["label"] for w in manifest.get("warnings") or [] if w.get("code") == PMTILES_FAILED]


def missing_tiles(manifest: dict) -> list[str]:
    """Spatial layers still without PMTiles: a PMTILES_FAILED warning, or no `pmtiles` flag and no
    NO_GEOMETRY warning. These are what the self-heal tiles."""
    failed, blank = _warned(manifest, PMTILES_FAILED), _warned(manifest, NO_GEOMETRY)
    return [e["label"] for e in manifest.get("layers") or [] if e.get("spatial") and (
        e["label"] in failed or not (e.get("pmtiles") or e["label"] in blank))]


class Needs(NamedTuple):
    """What a clean manifest still lacks, kept in place of the manifest: the spatial layers with no
    PMTiles, whether all of those failed tiling before (a retry, ordered after new work), and
    whether its relationships are missing or outdated, whether its layer styles are missing or
    outdated, whether its GeoPackage is missing or built from other inputs, and whether its unit
    product is missing or outdated."""
    tiles: tuple[str, ...]
    retry: bool
    relations: bool
    styles: bool = False
    geopackage: bool = False
    unit_polys: bool = False

    def __bool__(self) -> bool:
        return (bool(self.tiles) or self.relations or self.styles or self.geopackage
                or self.unit_polys)


def needs_of(manifest: dict) -> Needs:
    missing = missing_tiles(manifest)
    return Needs(tuple(missing), set(missing) <= _warned(manifest, PMTILES_FAILED),
                 needs_relations(manifest), needs_styles(manifest), needs_geopackage(manifest),
                 needs_unit_polys(manifest))


def is_clean(doc: dict) -> bool:
    """True for a manifest that records per-layer metadata and no errors. One with no `layers`
    list predates that metadata, so ordinary resumable runs re-extract it instead of needing
    --force."""
    return "layers" in doc and not doc.get("errors")


def has_layers(doc: dict) -> bool:
    """True for a manifest that records per-layer metadata, whatever its errors. The derived
    products (tiles, styles, relationships, GeoPackage, unit polygons) are built from the layers
    that extracted even when others of the pub failed."""
    return isinstance(doc.get("layers"), list)


def is_current(doc: dict) -> bool:
    """True for a clean manifest written at the current coverage (`EXTRACT_VERSION`). A clean one
    below it is still published from, but the next regular run extracts the pub again."""
    return is_clean(doc) and (doc.get("extract_version") or 0) >= EXTRACT_VERSION


def _load_manifest(path: str) -> dict:
    doc = json.loads(gcs.get_bytes(path).decode())
    if not isinstance(doc, dict):
        raise ValueError("not a JSON object")
    return doc


def _read_manifest(path: str) -> dict | None:
    """The manifest at `path` when it is clean (`is_clean`), else None. An unreadable manifest is
    reported and counts as not done."""
    try:
        doc = _load_manifest(path)
    except Exception as e:
        print(f"  unreadable manifest {path}, re-extracting: {_error(e)}", file=sys.stderr)
        return None
    return doc if is_clean(doc) else None


def _healable_manifest_of(series_id: str) -> dict | None:
    """A series' manifest when it exists, is readable and records layers (`has_layers`),
    errors or not, from its own listing."""
    path = f"{VECTORS_PREFIX}/{series_id}/_manifest.json"
    if path not in gcs.list_paths(f"{VECTORS_PREFIX}/{series_id}/"):
        return None
    try:
        doc = _load_manifest(path)
    except Exception as e:
        print(f"  unreadable manifest {path}, re-extracting: {_error(e)}", file=sys.stderr)
        return None
    return doc if has_layers(doc) else None


def _clean_manifest_of(series_id: str) -> dict | None:
    """A series' manifest when it exists and is clean (`_read_manifest`), from its own listing."""
    path = f"{VECTORS_PREFIX}/{series_id}/_manifest.json"
    return _read_manifest(path) if path in gcs.list_paths(f"{VECTORS_PREFIX}/{series_id}/") else None


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
    except FileNotFoundError:
        return {}
    except Exception as e:
        print(f"  unreadable attempt record {path}, retrying: {_error(e)}", file=sys.stderr)
        return {}
    return doc if isinstance(doc, dict) else {}


def _stable_errors(errors: list[dict], work: str) -> list[dict]:
    """`errors` with the run's download folder replaced by `<download>`, so the same failure reads
    the same in every run's attempt record."""
    return [{k: v.replace(work, "<download>") if isinstance(v, str) else v for k, v in e.items()}
            for e in errors]


# Errors that mean the source itself is bad: no projection, a file GDAL cannot open or read, a
# broken zip, invalid geometry, a link that is gone, or a container this image cannot read.
DATA_ERRORS = ("ValueError: no CRS", "DataSourceError:", "DataLayerError:", "FeatureError:",
               "FieldError:", "GeometryError:", "CRSError:", "BadZipFile:", "GEOSException:",
               "HTTPError: 404", "HTTPError: 410", "unsupported format")


def _data_failure_of(record: dict) -> tuple | None:
    """What a failed attempt failed on (result, extractor version, errors) when every error is a
    data problem (`DATA_ERRORS`). None otherwise: a code error, a server error or a timeout is never
    a known data problem, however often it repeats."""
    errors = record.get("errors") or []
    if record.get("result") != "fail:layers" or not errors or not all(
            str(e.get("error", "")).startswith(DATA_ERRORS) for e in errors):
        return None
    pairs = sorted((str(e.get("label") or e.get("source")), str(e.get("error"))) for e in errors)
    return record["result"], record.get("extract_version"), tuple(pairs)


def manifest_paths() -> list[str]:
    """Every series' `_manifest.json`, from one listing of the vectors prefix."""
    pfx = VECTORS_PREFIX.rstrip("/") + "/"
    return [p for p in gcs.list_paths(pfx) if p.endswith("/_manifest.json")]


def manifest_series(path: str) -> str:
    return path.removeprefix(VECTORS_PREFIX.rstrip("/") + "/").split("/", 1)[0]


@dataclass(frozen=True)
class Scanned:
    """One manifest after `scan_manifests`: what `reduce` kept of it, whether it is clean, and why
    it could not be read, if it could not."""
    path: str
    value: object = None
    clean: bool = False
    error: str | None = None


def scan_manifests(reduce, paths: list[str] | None = None) -> dict[str, Scanned]:
    """{series id: Scanned} for each manifest in `paths` (every one by default), read once on 16
    threads. `reduce(series_id, doc)` runs in the reading thread and keeps only what the caller
    needs, so a whole catalog of full manifests is never in memory at once."""
    def one(path: str) -> Scanned:
        try:
            doc = _load_manifest(path)
            return Scanned(path, reduce(manifest_series(path), doc), is_clean(doc))
        except Exception as e:  # noqa: BLE001 (returned per series; the caller reports it)
            return Scanned(path, error=_error(e))

    paths = manifest_paths() if paths is None else paths
    with ThreadPoolExecutor(max_workers=16) as ex:
        return {manifest_series(p): sc for p, sc in zip(paths, ex.map(one, paths))}


def clean_manifests(reduce=lambda series_id, doc: doc) -> dict[str, object]:
    """{series id: reduce(series id, manifest)} for each series extracted cleanly: a readable
    `_manifest.json` that lists no errors. A series with errors, or with layers but no manifest, is
    left out, so every run retries it and reports it again instead of the failure going quiet. An
    unreadable manifest is reported and counts as not done."""
    out: dict[str, object] = {}
    for sid, sc in scan_manifests(reduce).items():
        if sc.error:
            print(f"  unreadable manifest {sc.path}, re-extracting: {sc.error}", file=sys.stderr)
        elif sc.clean:
            out[sid] = sc.value
    return out


def healable_manifests(reduce=lambda series_id, doc: doc) -> dict[str, object]:
    """Like `clean_manifests`, but for every readable manifest that records layers
    (`has_layers`), with or without errors. These are the series the self-heal builds products for;
    the series with errors are still extracted again (they are not clean), so their failures are
    reported and retried."""
    out: dict[str, object] = {}
    for sid, sc in scan_manifests(lambda sid, doc: (has_layers(doc), reduce(sid, doc))).items():
        if sc.error:
            print(f"  unreadable manifest {sc.path}, re-extracting: {sc.error}", file=sys.stderr)
        elif sc.value[0]:
            out[sid] = sc.value[1]
    return out


def extracted_series() -> set[str]:
    return set(clean_manifests(lambda sid, doc: None))


def _fetch_all(work: str, files: list) -> tuple[list[tuple[str, str | None, str, bool]],
                                               list[dict], dict[str, Origin]]:
    """Download each attachment of `files` (`harvest.DataFile`) into its own folder under `work`,
    unzip zips and the zips inside them, and list the sources of all of them. The unzip limit
    covers the whole publication. Labels stay unique across attachments, and `origins` maps each to
    the attachment it came from. An attachment that cannot be fetched or unzipped is one error
    named for its title, and the others still extract."""
    srcs: list[tuple[str, str | None, str, bool]] = []
    errors: list[dict] = []
    origins: dict[str, Origin] = {}
    used: set[str] = set()
    for i, f in enumerate(files, 1):
        sub = os.path.join(work, f"{i:02d}_{_safe(f.key or 'file')[:40]}")
        os.makedirs(sub)
        remaining = MAX_UNZIPPED_BYTES - _tree_bytes(work)
        try:
            name = _safe(unquote(urlsplit(f.url).path.rsplit("/", 1)[-1])) or "attachment"
            dest = os.path.join(sub, name)
            harvest.download(harvest.encode_url(f.url), dest)
            nested_errors: list[dict] = []
            nested_dirs: list[str] = []
            if name.lower().endswith(".zip"):
                remaining -= _extract_zip(dest, sub, remaining)
                nested_errors, nested_dirs = _unzip_nested(sub, remaining)
            found, found_errors = _sources(sub, nested_dirs, used)
        except Exception as e:
            errors.append({"source": f.title, "error": _error(e)})
            print(f"  attachment {f.title!r} failed: {e}", file=sys.stderr)
            shutil.rmtree(sub, ignore_errors=True)
            continue
        errors += nested_errors + found_errors
        srcs += found
        origins.update({label: Origin(sub, f.key, f.title) for _, _, label, _ in found})
    return srcs, errors, origins


def extract_one(series_id: str, dry_run: bool = False, force: bool = False,
                existing: set[str] | None = None, untiled: list[str] | None = None,
                unpackaged: list[str] | None = None,
                without_units: list[str] | None = None,
                repeated: list[str] | None = None) -> str:
    """Extract and upload all vector layers and non-spatial companion tables for a given
    series_id. Each layer whose tiling failed is appended to `untiled` as `SID/label`, a
    GeoPackage that could not be built to `unpackaged`, and a unit product that could not be built
    to `without_units`. A pub whose layers fail on bad data exactly as in its last attempt is
    appended to `repeated`.

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
            manifest = _clean_manifest_of(series_id)
            done = manifest is not None and is_current(manifest)
        if done:
            print(f"{series_id}: SKIP (already extracted cleanly)")
            return "skip"

    files = harvest.data_files(series_id)
    if not files:
        # No attachments row: the optional manifest CSV or the site's data.php may still name a zip.
        _, gis_url = harvest.zip_urls(series_id)
        files = [harvest.DataFile(harvest.asset_key_of(series_id, gis_url), "GIS", gis_url)
                 ] if gis_url else []

    if not files:
        print(f"{series_id}: SKIP (no data zip or spreadsheet attachments)")
        return "skip"

    if dry_run:
        print(f"[dry-run] {series_id}: would download {', '.join(f.url for f in files)} "
              "and extract vector layers and tables")
        return "ok"

    prior = _data_failure_of(_last_attempt(series_id)) if repeated is not None else None

    def record_failure(record: dict) -> None:
        _record_attempt(series_id, record)
        if prior is not None and _data_failure_of({"extract_version": EXTRACT_VERSION, **record}) == prior:
            repeated.append(series_id)

    # Recorded before the download, so a run killed mid-pub (out of memory, job timeout) still
    # counts this pub as tried and orders it after the pubs never tried.
    _record_attempt(series_id, {"result": "started"})
    work = tempfile.mkdtemp(prefix=f"v_{series_id.replace('/', '_')}_")
    try:
        srcs, source_errors, origins = _fetch_all(work, files)
        if not srcs and not source_errors:
            print(f"{series_id}: SKIP (no shapefiles, GDB layers, or tables found)")
            _record_attempt(series_id, {"result": "skip"})
            return "skip"

        manifest = _extract_and_upload(work, series_id, srcs, source_errors, origins)
        failed_tiles = tiling_failures(manifest)
        if failed_tiles:
            print(f"{series_id}: {len(failed_tiles)} layers have no PMTiles (tiling failed): "
                  f"{', '.join(failed_tiles)}", file=sys.stderr)
            if untiled is not None:
                untiled += [f"{series_id}/{lb}" for lb in failed_tiles]
        n_spatial, n_tables = len(manifest["spatial"]), len(manifest["tables"])
        errors = manifest["errors"]
        res = "ok"
        if errors:
            unreadable = ", ".join(e.get("label") or e["source"] for e in errors)
            # Only unreadable containers (.mdb, .mpk): a known gap, re-reported by later runs
            # without re-downloading, rather than a failure.
            res = ("unsupported" if all(e["error"].startswith("unsupported format") for e in errors)
                   else "fail:layers")
            print(f"{series_id}: {'UNSUPPORTED' if res == 'unsupported' else 'FAIL'} "
                  f"({n_spatial} vector layers + {n_tables} companion tables uploaded, "
                  f"{len(errors)} failed: {unreadable})", file=sys.stderr)
            record = {"result": res, "errors": _stable_errors(errors, work)}
            if res == "unsupported":
                _record_attempt(series_id, record)
            else:
                record_failure(record)
            if n_spatial + n_tables == 0:
                return res
        elif n_spatial + n_tables == 0:
            print(f"{series_id}: SKIP (every layer is empty)")
            _record_attempt(series_id, {"result": "skip", "warnings": manifest["warnings"]})
            return "skip"

        try:
            _, package_errors = _geopackage_series(series_id)
        except Exception as e:
            package_errors = [f"{series_id}: GeoPackage not built: {_error(e)}"]
            print(package_errors[0], file=sys.stderr)
        if unpackaged is not None:
            unpackaged += package_errors
        try:
            unit_errors = _unit_polys_series(series_id)
        except Exception as e:
            unit_errors = [f"{series_id}: unit polygons not built: {_error(e)}"]
            print(unit_errors[0], file=sys.stderr)
        if without_units is not None:
            without_units += unit_errors

        if res == "ok":
            print(f"{series_id}: OK ({n_spatial} vector layers + {n_tables} companion tables "
                  f"uploaded to {VECTORS_PREFIX}/{series_id}/)")
            _record_attempt(series_id, {"result": "ok"})
        return res
    except Exception as e:
        print(f"{series_id}: FAIL {e}", file=sys.stderr)
        record_failure({"result": f"fail:{type(e).__name__}",
                        "errors": _stable_errors([{"source": "gis zip", "error": _error(e)}], work)})
        return f"fail:{type(e).__name__}"
    finally:
        shutil.rmtree(work, ignore_errors=True)



def _update_manifest(series_id: str, change) -> object:
    """Read a series' manifest, let `change(manifest)` edit it in place, and write it back with
    `put_bytes_if_unchanged`, so a manifest rewritten meanwhile raises `gcs.Changed` instead of
    being overwritten. `change` returns a (result, keep) pair; nothing is written unless `keep` and
    the manifest differs. Returns `result`."""
    path = f"{VECTORS_PREFIX}/{series_id}/_manifest.json"
    raw, version = gcs.get_bytes_versioned(path)
    manifest = json.loads(raw.decode())
    if not isinstance(manifest, dict) or not isinstance(manifest.get("layers"), list):
        raise ValueError(f"{path} records no per-layer metadata; re-extract with --force")
    before = json.dumps(manifest)
    result, keep = change(manifest)
    if keep and json.dumps(manifest) != before:
        gcs.put_bytes_if_unchanged(json.dumps(manifest).encode(), path, version,
                                   content_type="application/json", cache_control=gcs.CACHE_MUTABLE)
    return result


def _tile_missing(series_id: str) -> tuple[int, list[str]]:
    """Build the PMTiles a series' manifest does not record yet (including layers with a
    PMTILES_FAILED warning), from the GeoParquet already on GCS, and record them in the manifest.
    Returns (layers built, an error per layer that failed). A layer's tiling warnings are replaced
    by this attempt's outcome."""
    def tile(manifest: dict) -> tuple[tuple[int, list[str]], bool]:
        manifest.setdefault("warnings", [])
        missing = set(missing_tiles(manifest))
        todo = [e for e in manifest["layers"] if e.get("spatial") and e["label"] in missing]
        built, errors = 0, []
        # The same per-series work dir extraction uses; it holds one layer's files at a time.
        with tempfile.TemporaryDirectory(prefix=f"v_{series_id.replace('/', '_')}_") as work:
            for entry in todo:
                label = entry["label"]
                local = os.path.join(work, f"{label}.parquet")
                try:
                    gcs.download(f"{VECTORS_PREFIX}/{series_id}/{label}.parquet", local)
                except Exception as e:
                    errors.append(f"{series_id}/{label}: {_error(e)}")
                    print(f"  {series_id}/{label}: GeoParquet unreadable: {e}", file=sys.stderr)
                    continue
                manifest["warnings"] = [w for w in manifest["warnings"] if not (
                    w.get("label") == label
                    and w.get("code") in (PMTILES_FAILED, NO_GEOMETRY, TILE_COLUMNS_DROPPED))]
                try:
                    built += int(_tile_into(entry, local, series_id, work, manifest))
                finally:
                    os.remove(local)
                errors += [f"{series_id}/{label}: {w['warning']}" for w in manifest["warnings"]
                           if w.get("label") == label and w.get("code") == PMTILES_FAILED]
        return (built, errors), True

    return _update_manifest(series_id, tile)


def needs_relations(manifest: dict) -> bool:
    """True when a manifest's relationships predate the current inference rules, or were never
    recorded (`relations.RELATIONS_VERSION`)."""
    return (manifest.get("relations_version") or 0) < relations.RELATIONS_VERSION


def needs_unit_polys(manifest: dict) -> bool:
    """True when a manifest predates the current unit product (`unit_polys.UNIT_POLYS_VERSION`) or
    never recorded one. A pub with no unit layer records the version too, so it is not tried again."""
    return (manifest.get("unit_polys_version") or 0) < unit_polys.UNIT_POLYS_VERSION


def needs_styles(manifest: dict) -> bool:
    """True when a tiled layer has no recorded style, or the styles predate
    `layer_style.STYLE_VERSION`. A layer still waiting for PMTiles is styled in the same heal that
    tiles it."""
    tiled = [e for e in manifest.get("layers") or [] if e.get("spatial") and e.get("pmtiles")]
    return bool(tiled) and ((manifest.get("style_version") or 0) < layer_style.STYLE_VERSION
                            or any(not e.get("style") for e in tiled))


def _style_series(series_id: str) -> list[str]:
    """Write the layer styles a series' manifest lacks, from the GeoParquet on GCS, and record them
    in the manifest (`_style_into`). Returns an error per layer whose style failed."""
    def style(manifest: dict) -> tuple[list[str], bool]:
        return _style_into(series_id, manifest), True

    return _update_manifest(series_id, style)


def _packaged(manifest: dict) -> tuple[list[dict], list[dict]]:
    """(layers, relationships) the GeoPackage holds: what the publication page lists, so GeMS
    validation layers, and relationships to them, are left out as in the catalog."""
    from .sink_stac import is_validation_layer  # sink_stac imports this module

    layers = [e for e in manifest.get("layers") or [] if not is_validation_layer(e["label"])]
    kept = {e["label"] for e in layers}
    rels = [r for r in manifest.get("relationships") or []
            if r["origin"] in kept and r["destination"] in kept]
    return layers, rels


def _geopackage_inputs(manifest: dict) -> str | None:
    """A digest of everything the GeoPackage is built from: each packaged layer's GeoParquet
    checksum and the relationships. None when a layer has no recorded checksum yet."""
    layers, rels = _packaged(manifest)
    if any(not e.get("checksum") for e in layers):
        return None
    doc = {"layers": sorted((e["label"], e["checksum"]) for e in layers), "relationships": rels}
    return hashlib.sha256(json.dumps(doc, sort_keys=True).encode()).hexdigest()


def needs_geopackage(manifest: dict) -> bool:
    """True when a manifest with layers has no GeoPackage record, one below `GEOPACKAGE_VERSION`,
    or one built from other layer files or relationships than it lists now. A pub skipped for size
    keeps its record, so it is not tried again until its inputs change."""
    if not manifest.get("layers"):
        return False
    made = manifest.get("geopackage")
    return (not isinstance(made, dict) or (made.get("version") or 0) < GEOPACKAGE_VERSION
            or made.get("inputs") != _geopackage_inputs(manifest))


def _record_layer_files(series_id: str, layers: list[dict]) -> None:
    """Fill in `bytes` and `checksum` for each layer extracted before they were recorded, from the
    GeoParquet's stored metadata, hashing the object when it carries none."""
    for entry in layers:
        if entry.get("bytes") and entry.get("checksum"):
            continue
        path = f"{VECTORS_PREFIX}/{series_id}/{entry['label']}.parquet"
        meta = gcs.get_file_meta(path)
        if meta is None or not meta.checksum:
            meta = gcs.hash_object(path)
        entry.update(bytes=meta.size, checksum=meta.checksum)


_LAYER_OPTIONS = {"FID": GPKG_FID_COLUMN}


def _append_layer(src: str, out: str, entry: dict) -> None:
    """Append the GeoParquet `src` to the GeoPackage `out` as the layer `entry["label"]`, in record
    batches so a layer is never held whole. A spatial layer keeps its own CRS (from the file's
    GeoParquet metadata) and loses only the bbox covering column; a table is written without
    geometry."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    import pyogrio
    from pyogrio.raw import write_arrow
    from pyproj import CRS

    pf = pq.ParquetFile(src)
    label = entry["label"]
    if not entry.get("spatial"):
        for batch in pf.iter_batches(batch_size=GPKG_BATCH_ROWS):
            pyogrio.write_dataframe(batch.to_pandas(), out, layer=label, driver="GPKG",
                                    append=os.path.exists(out), layer_options=_LAYER_OPTIONS)
        return
    geo = json.loads((pf.schema_arrow.metadata or {}).get(b"geo") or b"{}")
    name = geo.get("primary_column")
    col = (geo.get("columns") or {}).get(name)
    if not col:
        raise ValueError("no GeoParquet geometry metadata")
    # GeoParquet reads a missing CRS as lon/lat; extraction never writes a layer without one.
    crs = CRS.from_user_input(col["crs"]).to_wkt() if col.get("crs") else "EPSG:4326"
    covering = ((col.get("covering") or {}).get("bbox") or {}).get("xmin") or [None]
    # GeoParquet names a 3D type "MultiPolygon Z", which OGR takes with its space; no-space
    # "MultiPolygonZ" is rejected. Mixed or missing types fall back to Unknown (Z kept per feature).
    types = {str(t).strip() for t in col.get("geometry_types") or []}
    kind = types.pop() if len(types) == 1 else "Unknown"
    keep = [n for n in pf.schema_arrow.names if n != covering[0]]
    for batch in pf.iter_batches(batch_size=GPKG_BATCH_ROWS, columns=keep):
        write_arrow(pa.Table.from_batches([batch]), out, layer=label, driver="GPKG",
                    geometry_name=name, geometry_type=kind, crs=crs, append=os.path.exists(out),
                    layer_options=_LAYER_OPTIONS)


def _write_geopackage(series_id: str, manifest: dict, out: str, work: str) -> None:
    """Build the GeoPackage `out` from the manifest's layers, one GeoParquet downloaded, appended
    and deleted at a time, then the relationships table. Any layer that fails raises: a GeoPackage
    missing a layer would pass for the whole publication."""
    layers, rels = _packaged(manifest)
    wide = {e["label"]: len(e.get("columns") or []) for e in layers
            if len(e.get("columns") or []) >= GPKG_MAX_COLUMNS}
    # A relationship to a table that is not in the file would point at nothing.
    dropped = {label: sum(label in (r["origin"], r["destination"]) for r in rels) for label in wide}
    rels = [r for r in rels if r["origin"] not in wide and r["destination"] not in wide]
    for entry in layers:
        label = entry["label"]
        width = wide.get(label, 0)
        if label in wide:
            msg = (f"{label} left out of the GeoPackage: {width} columns, over SQLite's limit of "
                   f"{GPKG_MAX_COLUMNS}; {dropped[label]} relationships to it left out too")
            manifest["warnings"].append({"label": label, "code": GEOPACKAGE_TABLE_SKIPPED,
                                         "warning": msg})
            print(f"  {series_id}: {msg}", file=sys.stderr)
            continue
        local = os.path.join(work, f"{label}.parquet")
        gcs.download(f"{VECTORS_PREFIX}/{series_id}/{label}.parquet", local)
        try:
            _append_layer(local, out, entry)
        except Exception as e:
            raise RuntimeError(f"layer {label}: {_error(e)}") from e
        finally:
            os.remove(local)
    if rels:
        import pandas as pd
        import pyogrio

        frame = pd.DataFrame({
            "origin_table": [r["origin"] for r in rels],
            "origin_columns": [", ".join(r["origin_cols"]) for r in rels],
            "destination_table": [r["destination"] for r in rels],
            "destination_columns": [", ".join(r["destination_cols"]) for r in rels],
            "detected_by": [r.get("detected_by") for r in rels],
            "confidence": [r.get("confidence") for r in rels]})
        pyogrio.write_dataframe(frame, out, layer=GPKG_RELATIONSHIPS, driver="GPKG", append=True)


def _geopackage_into(series_id: str, manifest: dict) -> list[str]:
    """Build and upload the pub's GeoPackage (`geopackage_object`) when `needs_geopackage`, and
    record `geopackage` {bytes, checksum, version, inputs} in `manifest`. A pub whose GeoParquet
    totals over `MAX_GPKG_BYTES` gets a GEOPACKAGE_SKIPPED warning and no file; a failed build gets
    GEOPACKAGE_FAILED and no record, so the next heal tries again. Returns an error for a failure."""
    codes = (GEOPACKAGE_SKIPPED, GEOPACKAGE_FAILED, GEOPACKAGE_STALE, GEOPACKAGE_TABLE_SKIPPED)
    skipped = [w for w in manifest.get("warnings") or []
               if w.get("code") in (GEOPACKAGE_SKIPPED, GEOPACKAGE_TABLE_SKIPPED)]
    manifest["warnings"] = [w for w in manifest.get("warnings") or [] if w.get("code") not in codes]
    if not needs_geopackage(manifest):
        manifest["warnings"] += skipped  # a skip stays on the manifest until its inputs change
        return []
    previous = manifest.pop("geopackage", None)
    try:
        _record_layer_files(series_id, manifest["layers"])
        total = sum(e["bytes"] for e in _packaged(manifest)[0])
        inputs = _geopackage_inputs(manifest)
        if total > MAX_GPKG_BYTES:
            manifest["warnings"].append({
                "code": GEOPACKAGE_SKIPPED,
                "warning": f"no GeoPackage published: the layers' GeoParquet totals "
                           f"{total / 2**30:.2f} GiB, over the {MAX_GPKG_BYTES / 2**30:g} GiB limit"})
            manifest["geopackage"] = {"version": GEOPACKAGE_VERSION, "inputs": inputs}
            return []
        with tempfile.TemporaryDirectory(prefix=f"g_{series_id.replace('/', '_')}_") as work:
            out = os.path.join(work, "pub.gpkg")
            _write_geopackage(series_id, manifest, out, work)
            meta = gcs.upload(out, geopackage_object(series_id), content_type=GEOPACKAGE_MIME,
                              cache_control=gcs.CACHE_MUTABLE)
        manifest["geopackage"] = {"bytes": meta.size, "checksum": meta.checksum,
                                  "version": GEOPACKAGE_VERSION, "inputs": inputs}
        return []
    except Exception as e:
        if isinstance(previous, dict) and previous.get("bytes"):
            # The last good file is still uploaded; its record stays so the Collection keeps the asset.
            manifest["geopackage"] = previous
            manifest["warnings"].append({
                "code": GEOPACKAGE_STALE,
                "warning": f"GeoPackage rebuild failed, the earlier file is kept: {_error(e)}"})
        else:
            manifest["warnings"].append({"code": GEOPACKAGE_FAILED,
                                         "warning": f"GeoPackage not built: {_error(e)}"})
        print(f"  {series_id}: GeoPackage not built: {_error(e)}", file=sys.stderr)
        return [f"{series_id}: GeoPackage not built: {_error(e)}"]


def _geopackage_series(series_id: str) -> tuple[bool, list[str]]:
    """Build the series' GeoPackage from the GeoParquet on GCS when it is missing or its inputs
    changed (`_geopackage_into`) and record it in the manifest. Returns (built, errors)."""
    def package(manifest: dict) -> tuple[tuple[bool, list[str]], bool]:
        manifest.setdefault("warnings", [])
        needed = needs_geopackage(manifest)
        errors = _geopackage_into(series_id, manifest)
        built = needed and not errors and bool((manifest.get("geopackage") or {}).get("bytes"))
        return (built, errors), True

    return _update_manifest(series_id, package)


def _unit_polys_series(series_id: str) -> list[str]:
    """Build the series' `unit_polys` GeoParquet and PMTiles from its extracted unit layer
    (`unit_polys.build`) and record them in the manifest. Returns an error when the build failed;
    the manifest then carries a UNIT_POLYS_FAILED warning and no version, so the next heal tries
    again."""
    def build(manifest: dict) -> tuple[list[str], bool]:
        manifest.setdefault("warnings", [])
        try:
            unit_polys.build(series_id, manifest)
        except Exception as e:
            manifest["warnings"] = [w for w in manifest["warnings"]
                                    if w.get("code") != unit_polys.UNIT_POLYS_FAILED]
            manifest["warnings"].append({"code": unit_polys.UNIT_POLYS_FAILED,
                                         "warning": f"unit polygons not built: {_error(e)}"})
            manifest.pop("unit_polys_version", None)
            print(f"  {series_id}: unit polygons not built: {_error(e)}", file=sys.stderr)
            return [f"{series_id}: unit polygons not built: {_error(e)}"], True
        return [], True

    return _update_manifest(series_id, build)


def _relations_series(series_id: str) -> list[str]:
    """Record a series' relationships, layer kinds and sources in its manifest, from the string and
    integer columns of its GeoParquet on GCS, read with range requests so no geometry is fetched.
    Declared relationships already recorded are kept: the geodatabase is not at hand to read them
    again. Returns an error per layer that could not be read; then nothing is written, since joins
    inferred without it could be wrong."""
    import pyarrow.parquet as pq

    def record(manifest: dict) -> tuple[list[str], bool]:
        manifest["warnings"] = [w for w in manifest.get("warnings") or [] if w.get("code") not in (
            relations.PROFILE_FAILED, relations.SCOPE_UNKNOWN)]
        if "relationships" not in manifest:
            manifest["warnings"].append({
                "code": relations.RELATIONSHIPS_NOT_READ,
                "warning": "declared relationships not read: computed from the extracted "
                           "GeoParquet, without the geodatabase; re-extract with --force to read "
                           "them"})
        known = {e["label"] for e in manifest["layers"]} | set(manifest.get("empty") or []) \
            | {e["label"] for e in manifest.get("errors") or [] if e.get("label")}
        several_folders = relations.collided(known)
        unscoped = [e["label"] for e in manifest["layers"]
                    if several_folders and "source" not in e and "__" not in e["label"]]
        if unscoped:
            manifest["warnings"].append({
                "code": relations.SCOPE_UNKNOWN,
                "warning": f"left out of relationship inference, folder unknown: "
                           f"{', '.join(unscoped)}; re-extract with --force to record it"})
        profiles: list[relations.Layer] = []
        errors: list[str] = []
        for entry in manifest["layers"]:
            label = entry["label"]
            if label in unscoped:  # no source recorded, so a later run still knows it
                continue
            entry.setdefault("source", relations.source_of_label(label, series_id))
            try:
                with gcs.open_reader(f"{VECTORS_PREFIX}/{series_id}/{label}.parquet") as f:
                    pf = pq.ParquetFile(f)
                    columns, _ = _columns_of(pf.schema_arrow)
                    keyed = [c["name"] for c in columns if c["type"] in ("string", "integer")]
                    table = pf.read(columns=keyed)
                spatial = bool(entry.get("spatial"))
                profiled, failed = relations.profile_table(table, keys=not spatial)
                del table
                profiles.append(relations.Layer(label, relations.layer_name(label), entry["source"],
                                                profiled, spatial))
                _warn_unprofiled(manifest, label, failed)
            except Exception as e:
                errors.append(f"{series_id}/{label}: {_error(e)}")
                print(f"  {series_id}/{label}: not read for relationships: {e}", file=sys.stderr)
        if errors:
            return errors, False
        declared = [r for r in manifest.get("relationships") or []
                    if r.get("detected_by") == "declared"]
        _set_relationships(manifest, relations.merge(declared, relations.infer(profiles)))
        return [], True

    return _update_manifest(series_id, record)


# Extraction, then self-heal, stop starting pubs or series once the run has used this much time,
# leaving room to finish the one in progress inside the job's 7200 s task timeout.
TIME_BUDGET_S = float(os.environ.get("VECTORS_TIME_BUDGET_S", "2700"))


@dataclass
class Heal:
    """One self-heal pass: layers tiled, pubs whose relationships were recorded, an error per
    layer or pub that failed this run (also grouped by series id), and the work the time budget
    left for the next run."""
    tiled: int = 0
    related: int = 0
    styled: int = 0
    packaged: int = 0
    unit_built: int = 0
    units_upgraded: int = 0
    failed: list[str] = field(default_factory=list)
    failed_by_series: dict[str, list[str]] = field(default_factory=dict)
    pending_layers: int = 0
    pending_pubs: int = 0
    pending_units: int = 0
    touched: set[str] = field(default_factory=set)  # series whose manifest the heal may have rewritten
    tiles: bool = True  # False for a heal that leaves tiling and styles to the vectors job

    def summary(self, next_step: str = "run") -> str:
        pending = self.pending_layers or self.pending_pubs or self.pending_units
        layers = f"{self.pending_layers} layers, " if self.tiles else ""
        return ("Self-heal: " + (f"{self.tiled} layers tiled, " if self.tiles else "")
                + f"{self.related} pubs' relationships recorded, units upgraded: {self.units_upgraded}"
                + (f", {self.styled} pubs' layer styles written, {self.packaged} GeoPackages built, "
                   f"{self.unit_built} pubs' unit polygons built" if self.tiles else "")
                + (f"; {layers}{self.pending_pubs} pubs and {self.pending_units} "
                   f"units files still pending, the next {next_step} continues" if pending else "")
                + (f"; {len(self.failed)} failed" if self.failed else ""))

    def fail(self, sid: str, errs: list[str]) -> None:
        if errs:
            self.failed += errs
            self.failed_by_series.setdefault(sid, []).extend(errs)


def units_objects(series_ids: set[str] | None = None) -> dict[str, str]:
    """{series id: object path} of the per-pub units GeoParquet files
    (`<UNITS_PREFIX>/<SID>/<SID>.units.parquet`), from one listing; `series_ids` (upper-case)
    narrows them."""
    pfx = identity.UNITS_PREFIX.rstrip("/") + "/"
    out: dict[str, str] = {}
    for path in gcs.list_paths(pfx):
        parts = path[len(pfx):].split("/")
        if (len(parts) == 2 and parts[1] == f"{parts[0]}.units.parquet"
                and (series_ids is None or parts[0].upper() in series_ids)):
            out[parts[0]] = path
    return out


# The first range read for a Parquet footer; a footer larger than this is read again in full.
FOOTER_PROBE_BYTES = 64 * 1024


def units_outdated(path: str) -> bool:
    """True when a GeoParquet file is older than 1.1 or has no bbox covering, read from its Parquet
    footer alone (a range read)."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    footer = gcs.get_tail(path, FOOTER_PROBE_BYTES)
    size = int.from_bytes(footer[-8:-4], "little") + 8
    if size > len(footer):
        footer = gcs.get_tail(path, size)
    meta = pq.read_metadata(pa.BufferReader(b"PAR1" + footer[-size:]))
    geo = json.loads((meta.metadata or {}).get(b"geo") or b"{}")
    version = tuple(int(n) for n in re.findall(r"\d+", str(geo.get("version") or ""))[:2])
    column = (geo.get("columns") or {}).get(geo.get("primary_column")) or {}
    return version < (1, 1) or "bbox" not in (column.get("covering") or {})


def _upgrade_units(path: str) -> None:
    """Rewrite a units GeoParquet through `geoparquet.write` (1.1, bbox covering) in place. The
    upload is MUTABLE, since the path is now rewritten."""
    import geopandas as gpd

    with tempfile.TemporaryDirectory(prefix="units_") as work:
        old, new = os.path.join(work, "old.parquet"), os.path.join(work, "new.parquet")
        gcs.download(path, old)
        gdf = gpd.read_parquet(old)
        os.remove(old)
        geoparquet.write(gdf, new)
        del gdf
        gcs.upload(new, path, content_type=PARQUET_MIME, cache_control=gcs.CACHE_MUTABLE)


def self_heal(needs: dict[str, Needs], *, deadline: float | None = None,
              units: dict[str, str] | None = None, tiles: bool = True) -> Heal:
    """Fill in the derivatives clean manifests are missing, from their GeoParquet on GCS and without
    downloading any zip: PMTiles for spatial layers that have none, relationships for manifests
    that need them, the layer styles for those that need them, then the GeoPackage of those
    whose layers or relationships changed, and the unit polygons of those without them. `needs` is `{series id: Needs}`
    (`needs_of`); each series' manifest is read again only when it is healed. Then upgrade each of the `units` files ({series id: object path}) that
    `units_outdated` says is older than GeoParquet 1.1. Series with only a failed layer to retry go
    last, as extraction orders retries last. No new series or units file starts once
    `time.monotonic()` passes `deadline`; the one in progress finishes, and what is left is counted
    as pending, not failed. With `tiles` False only relationships and units are healed: tiling and
    styling, the GeoPackage and the unit polygons are the vectors job's, sized for it, and a caller
    with less memory leaves them alone."""
    heal = Heal(tiles=tiles)
    if not tiles:
        needs = {sid: n._replace(tiles=(), retry=False, styles=False, geopackage=False,
                                 unit_polys=False) for sid, n in needs.items()}
    todo = sorted(((sid, n) for sid, n in needs.items() if n), key=lambda sn: sn[0])
    todo.sort(key=lambda sn: sn[1].retry and not sn[1].relations and not sn[1].styles
               and not sn[1].geopackage and not sn[1].unit_polys)
    for sid, need in todo:
        if deadline is not None and time.monotonic() >= deadline:
            heal.pending_layers += len(need.tiles)
            heal.pending_pubs += int(need.relations or need.styles or need.geopackage
                                     or need.unit_polys)
            continue
        errs: list[str] = []
        heal.touched.add(sid)
        try:
            built = 0
            if need.tiles:
                built, tile_errs = _tile_missing(sid)
                heal.tiled += built
                errs += tile_errs
            if need.relations:
                rel_errs = _relations_series(sid)
                heal.related += int(not rel_errs)
                errs += rel_errs
            if need.styles or built:
                style_errs = _style_series(sid)
                heal.styled += int(not style_errs)
                errs += style_errs
            # Packaged last: the relationships are one of its inputs. A heal that left them
            # unrecorded packages nothing, since the file would list the old ones. Only the
            # vectors job packages: a relations change in ingest leaves the file to its next run.
            if need.geopackage or (need.relations and heal.tiles):
                if not (need.relations and rel_errs):
                    packaged, pack_errs = _geopackage_series(sid)
                    heal.packaged += int(packaged)
                    errs += pack_errs
            # After the relationships: a GeMS pub's names and ages join on the recorded one.
            if need.unit_polys and not (need.relations and rel_errs):
                unit_errs = _unit_polys_series(sid)
                heal.unit_built += int(not unit_errs)
                errs += unit_errs
        except Exception as e:
            errs.append(f"{sid}: {_error(e)}")
            print(f"{sid}: self-heal FAIL {_error(e)}", file=sys.stderr)
        heal.fail(sid, errs)
    _heal_units(heal, units or {}, deadline)
    return heal


def _heal_units(heal: Heal, units: dict[str, str], deadline: float | None) -> None:
    """Upgrade the outdated units files into `heal`, within `deadline`. Footers are read in
    parallel; a file whose footer or rewrite fails is a failure for its series."""
    def check(item: tuple[str, str]) -> tuple[str, str, bool | str]:
        sid, path = item
        try:
            return sid, path, units_outdated(path)
        except Exception as e:
            return sid, path, _error(e)

    with ThreadPoolExecutor(max_workers=16) as ex:
        checked = list(ex.map(check, sorted(units.items())))
    for sid, path, outdated in checked:
        if isinstance(outdated, str):
            heal.fail(sid, [f"{path}: footer unreadable: {outdated}"])
        elif not outdated:
            continue
        elif deadline is not None and time.monotonic() >= deadline:
            heal.pending_units += 1
        else:
            try:
                _upgrade_units(path)
                heal.units_upgraded += 1
            except Exception as e:
                heal.fail(sid, [f"{path}: units not upgraded: {_error(e)}"])
                print(f"{path}: units not upgraded: {_error(e)}", file=sys.stderr)


def main() -> int:
    started = time.monotonic()
    ap = argparse.ArgumentParser(description="Extract GIS publication vector layers to GCS")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("series_id", nargs="*", default=[], help="Series ID(s) to extract")
    g.add_argument("--all", action="store_true", help="Extract all series IDs from metadata")
    ap.add_argument("--limit", type=int, default=None, help="Limit number of publications to extract")
    ap.add_argument("--dry-run", action="store_true", help="Dry run (check URLs and schema only)")
    ap.add_argument("--force", action="store_true", help="Force extraction even if artifacts exist")
    args = ap.parse_args()
    if not args.all and not args.series_id:
        ap.error("give series ids or --all")

    sids = []
    all_sids: list[str] = []
    existing = None
    needs: dict[str, Needs] = {}
    if args.all:
        print(f"Loading publications from source: {source.source_name()}")
        all_sids = [s for p in source.read_pubs() if (s := (p.get("series_id") or "").strip())]
        # A batch run only visits pubs with a data zip or spreadsheet attachment, and checks what
        # is already extracted with one listing, so a weekly run costs about nothing when nothing
        # is new.
        sids = [s for s in all_sids if harvest.data_files(s)]
        # A forced run re-extracts every pub, so nothing is left to heal. A manifest below
        # EXTRACT_VERSION is extracted again, not healed. A current one with errors is healed too,
        # so its extracted layers get their products, but only a clean one counts as extracted.
        listed = set(sids)
        found = {} if args.force else healable_manifests(
            lambda sid, doc: (needs_of(doc), (doc.get("extract_version") or 0) >= EXTRACT_VERSION,
                              is_current(doc)))
        needs = {s: n for s, (n, current, _) in found.items() if s in listed and current}
        existing = None if args.force else {s for s in needs if found[s][2]}
        unsupported: list[str] = []
        nothing: list[str] = []
        if existing is not None:
            tried = _series_with("_attempt.json", "_manifest.json")
            attempted = _series_with("_attempt.json")
            # A pub whose last attempt failed only on unreadable containers, or finished cleanly
            # with nothing to extract, is listed, not re-downloaded; --force retries it (for
            # example after the image gains a driver). An attempt made at an older
            # EXTRACT_VERSION does not count: the pub is tried again at the current coverage.
            for s in [s for s in sids if s in attempted and s not in existing]:
                last = _last_attempt(s)
                if (last.get("extract_version") or 0) < EXTRACT_VERSION:
                    continue
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
              f"{len(sids) + len(unsupported) + len(nothing)} with a data zip or spreadsheet, "
              f"{len(set(sids) & (existing or set()))} already extracted")
        if nothing:
            print(f"{len(nothing)} had no layers to extract last time, not retried (--force to "
                  f"retry): {', '.join(nothing)}")
        if unsupported:
            print(f"{len(unsupported)} contain formats this image cannot read (any readable "
                  f"layers were extracted), not retried (--force to retry): {', '.join(unsupported)}")
    else:
        sids = args.series_id

    # `--tasks=N`: each task extracts and heals only the pubs it owns by series id, so the split
    # holds even though tasks list the bucket at slightly different times. The ordered list keeps
    # new pubs first within each task. A targeted run is split the same way, so each named pub is
    # handled by one task. `--limit` applies per task.
    task_index, task_count = jobs.task()
    sharded = task_count > 1
    if sharded:
        sids = [s for s in sids if jobs.owns(identity.Pub.parse(s).series_id)]
        print(f"shard {task_index + 1}/{task_count}: {len(sids)} publications")
        if not args.all and not sids:
            print(f"shard {task_index + 1}/{task_count}: none of the named publications belong "
                  f"to this task")

    if args.limit:
        sids = sids[:args.limit]

    failed: list[str] = []
    # A batch run fails only on what is new: a pub whose layers fail on bad data exactly as last run
    # is a known data problem, retried every run and listed, not failed again. A targeted run
    # reports every failure.
    known: list[str] = []
    repeated: list[str] | None = [] if args.all else None
    untiled: list[str] = []
    unpackaged: list[str] = []
    without_units: list[str] = []
    extracted: set[str] = set()
    deadline = started + TIME_BUDGET_S
    # Only a regular batch run picks up what it leaves, so targeted and forced runs extract all.
    budgeted = args.all and not args.force
    for i, sid in enumerate(sids):
        if budgeted and time.monotonic() >= deadline:
            left = sids[i:]
            more = f", ... ({len(left) - 20} more)" if len(left) > 20 else ""
            print(f"Time budget used: {len(left)} publications left to extract, the next run "
                  f"continues: {', '.join(left[:20])}{more}")
            break
        res = extract_one(sid, dry_run=args.dry_run, force=args.force, existing=existing,
                          untiled=untiled, unpackaged=unpackaged,
                          without_units=without_units, repeated=repeated)
        if res != "skip":
            extracted.add(identity.Pub.parse(sid).series_id)
        if res.startswith("fail"):
            same = repeated is not None and identity.Pub.parse(sid).series_id in repeated
            (known if same else failed).append(f"{sid} ({res})")
    if args.all:
        # A pub with errors is both healable and extracted again; the extraction's result stands,
        # as in a targeted run. Pubs the time budget skipped stay in `needs` and still heal.
        needs = {s: n for s, n in needs.items() if s not in extracted}
    if not args.all and not args.force:
        # A targeted run heals the named pubs it did not extract this run, as `--all` heals only
        # the pubs already clean when it started: a layer whose tiling just failed is reported
        # once, by extraction, and retried by the next run.
        needs = {sid: needs_of(m) for s in sids
                 if (sid := identity.Pub.parse(s).series_id) not in extracted
                 and (m := _healable_manifest_of(sid))}

    # Units files come from the plate harvest, not from extraction, so every covered pub is checked.
    units = units_objects({identity.Pub.parse(s).series_id.upper()
                           for s in (all_sids if args.all else args.series_id)})
    if sharded:
        # The task that owns a pub also heals it, so no two tasks write one manifest or units file.
        needs = {sid: n for sid, n in needs.items() if jobs.owns(sid)}
        units = {sid: u for sid, u in units.items() if jobs.owns(sid)}
    identified = identify.Result()
    if args.dry_run:
        pending = sum(len(n.tiles) for n in needs.values())
        print(f"[dry-run] self-heal would tile {pending} layers, record relationships for "
              f"{sum(n.relations for n in needs.values())} pubs, build unit polygons for "
              f"{sum(n.unit_polys for n in needs.values())} pubs and check {len(units)} units files")
        heal = Heal()
    else:
        heal = self_heal(needs, deadline=deadline, units=units)
        print(heal.summary())
        if args.all and task_index == 0:
            # After the heal, so the tiers see the unit polygons it just built. A run the time
            # budget cut short leaves the rest to the next run, which sees their checksums change.
            # Catalog-wide (tier files, band cache, mosaic Items), so task 0 alone writes it, from
            # what is extracted so far.
            identified = identify.build_changed()
            print(f"Identify layers: built {identified.built or 'none'}, unchanged "
                  f"{identified.unchanged or 'none'}")
    # Exit 1 only for work that failed in THIS run: a pub that failed to extract (unless on the same
    # bad data as last run), a layer whose tiling failed at extraction, or a layer or pub whose
    # self-heal failed. Work the time budget left pending is not a failure; the next run continues it.
    if untiled:
        print(f"{len(untiled)} layers published without PMTiles (tiling failed this run; the next "
              f"run retries them): {', '.join(untiled)}", file=sys.stderr)
    if without_units:
        print(f"{len(without_units)} publications without unit polygons (the build failed this "
              f"run; the next run retries them): {'; '.join(without_units)}", file=sys.stderr)
    if unpackaged:
        print(f"{len(unpackaged)} publications published without a GeoPackage (the build failed this "
              f"run; the next run retries them): {'; '.join(unpackaged)}", file=sys.stderr)
    if heal.failed:
        more = f", ... ({len(heal.failed) - 20} more)" if len(heal.failed) > 20 else ""
        print(f"{len(heal.failed)} self-heal failures this run (the next run retries them): "
              f"{', '.join(heal.failed[:20])}{more}", file=sys.stderr)
    if identified.errors:
        print(f"{len(identified.errors)} identify failures this run (the next run retries them): "
              f"{'; '.join(identified.errors)}", file=sys.stderr)
    if known:
        print(f"{len(known)} publications failed the same way as their last run (data problems, "
              f"retried every run, not counted as failures): {', '.join(known)}")
    if failed:
        print(f"{len(failed)} of {len(sids)} publications failed: {', '.join(failed)}",
              file=sys.stderr)
    return 1 if (failed or untiled or unpackaged or without_units or heal.failed
                 or identified.errors) else 0


if __name__ == "__main__":
    sys.exit(main())
