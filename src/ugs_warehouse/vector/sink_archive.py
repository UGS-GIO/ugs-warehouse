"""Write the transformed topic to GeoParquet on GCS — native geometry, citable.

Three artifacts per ingest:
  {ARCHIVE_PREFIX}/{stem}/{stem}.parquet               (latest pointer, overwritten -> no-cache)
  {ARCHIVE_PREFIX}/{stem}/{stem}_{YYYYMMDD}.parquet    (dated archive, immutable -> long cache)
  {ARCHIVE_PREFIX}/{stem}/{stem}.flat.parquet          (latest, no nested column -> no-cache)

The latest pointer is the easy-to-link copy; dated archives are the citable snapshots.
GCS IO + bucket/prefix/CDN come from `core` (shared with the pubs producer).
"""
from __future__ import annotations

import datetime
import json
import os
import tempfile

import duckdb

from ..core import config, gcs
from .topics import Topic

PARQUET_MIME = config.PARQUET_MIME
GEOPARQUET_VERSION = "1.1.0"
FLAT_GEOPARQUET_VERSION = "1.0.0"

# A row group is the smallest unit a range-reading client can fetch, so it floors both the viewer's
# first page and what a clipped export has to download. DuckDB's default (122,880) put
# wetlands_riverine in 2 groups, the larger holding 1.06 GB — a 22-feature AOI still read all of it.
#
# A fixed row count does not bound that, because a row's weight is mostly its geometry and that
# varies by three orders of magnitude across topics: 10,000 rows is ~0.2 MB of points but ~113 MB
# of riverine's polylines. So target BYTES and derive the row count per topic. (DuckDB has
# ROW_GROUP_SIZE_BYTES, but it refuses to run while preserving insertion order, which would throw
# away the hilbert sort that makes the bbox stats prune at all.)
TARGET_ROW_GROUP_BYTES = 32 * 1024**2
ROW_GROUP_MIN = 512          # heavy geometry: a few hundred rows is already tens of MB
ROW_GROUP_MAX = 122_880      # DuckDB's own default, the ceiling for very light rows


def _row_group_size(con: duckdb.DuckDBPyConnection, view: str) -> int:
    """Rows per group so a group lands near TARGET_ROW_GROUP_BYTES, from the view's own geometry.

    Sampled, not scanned: the estimate only has to land the order of magnitude. The sample is the
    head of a hilbert-sorted view, so it is one region rather than a spread of them; that is
    adequate where feature complexity is roughly uniform and approximate where it is not.

    `geom` is excluded from the JSON term because to_json() would serialise it again as WKT, and
    charging the geometry twice put the groups at about half the target.
    """
    row = con.execute(
        f"SELECT (SELECT avg(octet_length(ST_AsWKB(geom))) FROM (SELECT geom FROM {view} LIMIT 20000)) "
        f"+ (SELECT avg(coalesce(len(to_json(u)), 0)) "
        f"   FROM (SELECT * EXCLUDE (geom) FROM {view} LIMIT 20000) u)"
    ).fetchone()
    per_row = float(row[0] or 0)
    if per_row <= 0:
        return ROW_GROUP_MAX
    return max(ROW_GROUP_MIN, min(ROW_GROUP_MAX, int(TARGET_ROW_GROUP_BYTES / per_row)))


# ST_GeometryType names → GeoParquet's.
_GEOMETRY_TYPES = {"POINT": "Point", "LINESTRING": "LineString", "POLYGON": "Polygon",
                   "MULTIPOINT": "MultiPoint", "MULTILINESTRING": "MultiLineString",
                   "MULTIPOLYGON": "MultiPolygon", "GEOMETRYCOLLECTION": "GeometryCollection"}


def _geo_metadata(con: duckdb.DuckDBPyConnection, view: str) -> str:
    """The GeoParquet 1.1 `geo` key: WKB in `geom`, the types present, and `bbox` as its covering.
    The CRS is left out, which GeoParquet reads as OGC:CRS84: the transform writes lon/lat WGS84."""
    rows = con.execute(f"SELECT DISTINCT ST_GeometryType(geom)::VARCHAR, ST_HasZ(geom) FROM {view} "
                       f"WHERE geom IS NOT NULL").fetchall()
    types = sorted({_GEOMETRY_TYPES.get(t, t) + (" Z" if z else "") for t, z in rows if t})
    covering = {k: ["bbox", k] for k in ("xmin", "ymin", "xmax", "ymax")}
    return json.dumps({"version": GEOPARQUET_VERSION, "primary_column": "geom", "columns": {
        "geom": {"encoding": "WKB", "geometry_types": types, "covering": {"bbox": covering}}}})


def _copy_geoparquet(con: duckdb.DuckDBPyConnection, view: str, path: str) -> int:
    """COPY the transformed `view` to a GeoParquet file.

    GeoParquet 1.1, written by hand: DuckDB writes 1.0, and its 2.0 (Parquet's native GEOMETRY
    type) does not open in GDAL before 3.12, which is every QGIS release today. The `bbox` struct is
    the 1.1 covering column; its per-row-group min/max lets any spec-aware reader skip row groups on
    a bbox (Portolan PTL-DAT-007, -012).

    The extent also goes out as four plain columns (bbox_xmin/ymin/xmax/ymax), which the viewer
    and ugs-map-viewer already read. Returns the rows per group, which the flat copy reuses.
    """
    rows_per_group = _row_group_size(con, view)
    print(f"[archive] row group size: {rows_per_group} rows (~{TARGET_ROW_GROUP_BYTES // 1024**2} MB)")
    geo = _geo_metadata(con, view)
    con.execute(
        f"COPY (SELECT * REPLACE (ST_AsWKB(geom) AS geom), "
        f"ST_XMin(geom) AS bbox_xmin, ST_YMin(geom) AS bbox_ymin, "
        f"ST_XMax(geom) AS bbox_xmax, ST_YMax(geom) AS bbox_ymax, "
        f"{{'xmin': ST_XMin(geom), 'ymin': ST_YMin(geom), 'xmax': ST_XMax(geom), 'ymax': ST_YMax(geom)}} AS bbox "
        f"FROM {view}) TO '{path}' "
        f"(FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE {rows_per_group}, "
        f"GEOPARQUET_VERSION 'NONE', KV_METADATA {{geo: '{geo}'}})"
    )
    return rows_per_group


def _copy_flat(src: str, path: str, rows_per_group: int) -> None:
    """The archive at `src` without its nested `bbox` column, as GeoParquet 1.0.

    ArcGIS Pro opens no Parquet with a nested column, and 1.1's covering must be one, so the copy
    for it is 1.0. Rows keep the archive's order and groups, so the flat bbox_* columns' min/max
    still prune for a reader that filters on them. A fresh connection with conversion off reads
    `geom` as the WKB it is rather than as a GEOMETRY value.
    """
    con = duckdb.connect()
    con.execute(f"SET max_memory = '{os.environ.get('DUCKDB_MAX_MEMORY', '2GB')}'")  # the ingest cap
    con.execute("SET enable_geoparquet_conversion = false")
    row = con.execute(f"SELECT decode(value) FROM parquet_kv_metadata('{src}') "
                      f"WHERE decode(key) = 'geo'").fetchone()
    geo = json.loads(row[0])
    geo["version"] = FLAT_GEOPARQUET_VERSION
    for column in geo["columns"].values():
        column.pop("covering", None)
    con.execute(
        f"COPY (SELECT * EXCLUDE (bbox) FROM read_parquet('{src}')) TO '{path}' "
        f"(FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE {rows_per_group}, "
        f"GEOPARQUET_VERSION 'NONE', KV_METADATA {{geo: '{json.dumps(geo).replace(chr(39), chr(39) * 2)}'}})"
    )


def _upload(topic: Topic, local: str) -> gcs.FileMeta:
    """Upload a finished GeoParquet as the latest pointer + a dated immutable snapshot."""
    stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d")
    base = f"{config.ARCHIVE_PREFIX}/{topic.stem}"
    latest = f"{base}/{topic.stem}.parquet"
    dated = f"{base}/{topic.stem}_{stamp}.parquet"
    meta = gcs.upload(local, latest, content_type=PARQUET_MIME, cache_control=gcs.CACHE_MUTABLE)
    gcs.upload(local, dated, content_type=PARQUET_MIME, cache_control=gcs.CACHE_IMMUTABLE)
    print(f"[{topic.fqn}] archive: {config.public_url(latest)} (+ dated {stamp})")
    return meta  # same bytes both times; the item cites the latest pointer


def is_current(topic: Topic) -> bool:
    """The published archive is in the format `write` makes today: GeoParquet 1.1 with a bbox
    covering. Skip-unchanged checks this, so an archive from an older writer is rebuilt even when its
    data has not changed. Reads only the Parquet footer."""
    path = config.archive_path(topic.stem)
    try:
        footer = gcs.get_tail(path, 64 * 1024)  # holds the whole footer of any archive we write
        size = int.from_bytes(footer[-8:-4], "little") + 8
        if size > len(footer):
            footer = gcs.get_tail(path, size)
    except FileNotFoundError:
        return False
    footer = footer[-size:]
    with tempfile.NamedTemporaryFile(suffix=".parquet") as f:
        f.write(b"PAR1" + footer)  # DuckDB reads the metadata from the end of the file
        f.flush()
        row = duckdb.sql(f"SELECT decode(value) FROM parquet_kv_metadata('{f.name}') "
                         f"WHERE decode(key) = 'geo'").fetchone()
    geo = json.loads(row[0]) if row else {}
    column = (geo.get("columns") or {}).get(geo.get("primary_column")) or {}
    return geo.get("version") == GEOPARQUET_VERSION and "bbox" in (column.get("covering") or {})


def write(topic: Topic, con: duckdb.DuckDBPyConnection, view: str) -> dict[str, gcs.FileMeta]:
    """Write `{stem}.parquet` (latest) + dated archive + the flat copy to GCS, keyed by the asset
    each one backs. DuckDB streams the COPY (with the global hilbert sort) under the memory cap →
    bounded memory regardless of table size."""
    with tempfile.TemporaryDirectory() as tmp:
        local = os.path.join(tmp, f"{topic.stem}.parquet")
        rows_per_group = _copy_geoparquet(con, view, local)
        meta = _upload(topic, local)
        return {"data": meta, "data_flat": _upload_flat(topic, local, tmp, rows_per_group)}


def flat_present(topic: Topic) -> bool:
    """The flat copy exists, so an unchanged topic needs nothing rebuilt for it."""
    return gcs.exists(config.archive_flat_path(topic.stem))


def write_flat(topic: Topic) -> dict[str, gcs.FileMeta]:
    """Only the flat copy, for a topic whose archive is current but predates it: derived from the
    published archive, so its rows and groups match that file and nothing is sorted again."""
    with tempfile.TemporaryDirectory() as tmp:
        local = os.path.join(tmp, f"{topic.stem}.parquet")
        with open(local, "wb") as fh:
            fh.write(gcs.get_bytes(config.archive_path(topic.stem)))
        con = duckdb.connect()
        rows_per_group = con.execute(f"SELECT max(row_group_num_rows) FROM parquet_metadata('{local}')"
                                     ).fetchone()[0]
        return {"data_flat": _upload_flat(topic, local, tmp, int(rows_per_group or ROW_GROUP_MAX))}


def _upload_flat(topic: Topic, local: str, tmp: str, rows_per_group: int) -> gcs.FileMeta:
    flat = os.path.join(tmp, f"{topic.stem}.flat.parquet")
    _copy_flat(local, flat, rows_per_group)
    path = config.archive_flat_path(topic.stem)
    meta = gcs.upload(flat, path, content_type=PARQUET_MIME, cache_control=gcs.CACHE_MUTABLE)
    print(f"[{topic.fqn}] archive (flat, GeoParquet {FLAT_GEOPARQUET_VERSION}): {config.public_url(path)}")
    return meta
