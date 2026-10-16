"""Write the transformed topic to GeoParquet on GCS — native geometry, citable.

Two artifacts per ingest:
  {ARCHIVE_PREFIX}/{stem}/{stem}.parquet               (latest pointer, overwritten -> no-cache)
  {ARCHIVE_PREFIX}/{stem}/{stem}_{YYYYMMDD}.parquet    (dated archive, immutable -> long cache)

The latest pointer is the easy-to-link copy; dated archives are the citable snapshots.
GCS IO + bucket/prefix/CDN come from `core` (shared with the pubs producer).
"""
from __future__ import annotations

import datetime
import json
import os
import tempfile

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from ..core import config, gcs
from .topics import Topic

PARQUET_MIME = config.PARQUET_MIME
GEOPARQUET_VERSION = "1.1.0"

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
PAGE_BYTES = 64 * 1024       # ~100 rows of polygon geometry: what a page-index reader fetches

# A statewide map of a heavy layer cannot be drawn from the archive without reading all of it, so
# heavy line and polygon layers also get a generalized copy. 0.0005 deg (~50 m) is finer than a
# pixel through zoom 11; nonriverine wetlands go from 891 MB of geometry to 10 MB.
OVERVIEW_TOLERANCE = 0.0005
OVERVIEW_MIN_BYTES = 64 * 1024**2


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


# DuckDB types the pyarrow writer cannot write, or writes differently from DuckDB's COPY: INTERVAL
# fails outright, BIT and BIGNUM come out as raw bytes, and TIMETZ drops its offset. COPY wrote
# HUGEINT as a DOUBLE. UUID and JSON reach arrow as plain strings and are tagged again below.
_AS_TEXT = {"INTERVAL", "BIT", "BIGNUM", "VARINT", "TIME WITH TIME ZONE"}
_AS_DOUBLE = {"HUGEINT", "UHUGEINT"}


def _replacements(con: duckdb.DuckDBPyConnection, view: str) -> tuple[list[str], list[str], list[str]]:
    """REPLACE terms for the archive SELECT, and the UUID and JSON columns to tag."""
    terms, uuids, jsons = ["ST_AsWKB(geom) AS geom"], [], []
    for name, typ, *_ in con.execute(f"DESCRIBE {view}").fetchall():
        col = '"' + name.replace('"', '""') + '"'
        if typ in _AS_TEXT:
            terms.append(f"{col}::VARCHAR AS {col}")
        elif typ in _AS_DOUBLE:
            terms.append(f"{col}::DOUBLE AS {col}")
        elif typ == "UUID":
            terms.append(f"unhex(replace({col}::VARCHAR, '-', '')) AS {col}")
            uuids.append(name)
        elif typ == "JSON":
            jsons.append(name)
    return terms, uuids, jsons


def _tagged(batch: pa.RecordBatch, uuids: list[str], jsons: list[str]) -> pa.RecordBatch:
    """The batch with its UUID and JSON columns as arrow's uuid and json types, which Parquet keeps
    as logical types."""
    cols = {n: pa.ExtensionArray.from_storage(pa.uuid(), batch[n].cast(pa.binary(16))) for n in uuids}
    cols |= {n: pa.ExtensionArray.from_storage(pa.json_(batch[n].type), batch[n]) for n in jsons}
    if not cols:
        return batch
    return pa.RecordBatch.from_arrays([cols.get(n, batch[n]) for n in batch.schema.names],
                                      names=batch.schema.names)


def _copy_geoparquet(con: duckdb.DuckDBPyConnection, view: str, path: str) -> None:
    """Stream the transformed `view` into a GeoParquet file.

    GeoParquet 1.1, written by hand: DuckDB writes 1.0, and its 2.0 (Parquet's native GEOMETRY
    type) does not open in GDAL before 3.12, which is every QGIS release today. The `bbox` struct is
    the 1.1 covering column; its per-row-group min/max lets any spec-aware reader skip row groups on
    a bbox (Portolan PTL-DAT-007, -012).

    The extent also goes out as four plain columns (bbox_xmin/ymin/xmax/ymax), which the viewer
    and ugs-map-viewer already read.

    pyarrow writes it, not DuckDB's COPY, for the page index. Row groups stay at the gpio-recommended
    size, and the page index lets a reader fetch only the ~100-row pages a bbox touches inside one
    (the FeatureServer and the viewer both read with hyparquet, which uses it). Only text and
    integer columns get a dictionary: on the geometry a dictionary page forces a reader back to the
    start of the column chunk, and on floats it makes the file bigger. write_batch_size lets pages
    close near PAGE_BYTES; at the default 1024 every page is at least 1024 rows.
    """
    rows_per_group = _row_group_size(con, view)
    print(f"[archive] row group size: {rows_per_group} rows (~{TARGET_ROW_GROUP_BYTES // 1024**2} MB)")
    geo = _geo_metadata(con, view)
    replace, uuids, jsons = _replacements(con, view)
    batches = con.execute(
        f"SELECT * REPLACE ({', '.join(replace)}), "
        f"ST_XMin(geom) AS bbox_xmin, ST_YMin(geom) AS bbox_ymin, "
        f"ST_XMax(geom) AS bbox_xmax, ST_YMax(geom) AS bbox_ymax, "
        f"{{'xmin': ST_XMin(geom), 'ymin': ST_YMin(geom), 'xmax': ST_XMax(geom), 'ymax': ST_YMax(geom)}} AS bbox "
        f"FROM {view}"
    ).to_arrow_reader(rows_per_group)
    schema = batches.schema
    tags = [(n, pa.uuid()) for n in uuids] + [(n, pa.json_(schema.field(n).type)) for n in jsons]
    for name, typ in tags:
        schema = schema.set(schema.get_field_index(name), pa.field(name, typ))
    schema = schema.with_metadata({"geo": geo})
    dictionary = [f.name for f in schema if f.name != "geom" and (
        pa.types.is_string(f.type) or pa.types.is_large_string(f.type) or pa.types.is_integer(f.type))]
    with pq.ParquetWriter(path, schema, compression="zstd", compression_level=9, write_page_index=True,
                          data_page_size=PAGE_BYTES, write_batch_size=64, use_dictionary=dictionary) as out:
        for batch in batches:   # one DuckDB batch per row group, so only one is ever in memory
            out.write_table(pa.Table.from_batches([_tagged(batch, uuids, jsons)], schema),
                            row_group_size=rows_per_group)


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
    covering and a page index. Skip-unchanged checks this, so an archive from an older writer is
    rebuilt even when its data has not changed. Reads only the Parquet footer."""
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
        meta = pq.read_metadata(f.name)
    geo = json.loads(row[0]) if row else {}
    column = (geo.get("columns") or {}).get(geo.get("primary_column")) or {}
    geom = meta.schema.names.index("geom") if "geom" in meta.schema.names else None
    paged = geom is not None and meta.num_row_groups > 0 and meta.row_group(0).column(geom).has_offset_index
    return (geo.get("version") == GEOPARQUET_VERSION and "bbox" in (column.get("covering") or {})
            and paged)


def write_overview(topic: Topic, con: duckdb.DuckDBPyConnection, view: str) -> gcs.FileMeta | None:
    """Write `{stem}_overview.parquet`, the archive simplified to OVERVIEW_TOLERANCE, when the
    layer is heavy enough to need one; otherwise remove a stale one. Rows keep their feature_id,
    so a feature drawn from the overview is the same feature in the archive."""
    path = config.overview_path(topic.stem)
    weight, points = con.execute(
        f"SELECT sum(octet_length(ST_AsWKB(geom))), bool_and(ST_GeometryType(geom)::VARCHAR LIKE '%POINT') "
        f"FROM {view}").fetchone()
    if points or (weight or 0) < OVERVIEW_MIN_BYTES:
        gcs.delete(path)
        return None
    # A polygon smaller than the tolerance keeps its envelope, a speck where it is, as the vector
    # tiles keep one; dropping it would empty a statewide view of a layer made of small features.
    con.execute(
        f"CREATE OR REPLACE TEMP VIEW {view}_overview AS SELECT * FROM (SELECT * EXCLUDE (_simple) "
        f"REPLACE (CASE WHEN ST_IsEmpty(_simple) AND ST_Dimension(geom) = 2 THEN ST_Envelope(geom) "
        f"ELSE _simple END AS geom) FROM (SELECT *, ST_Simplify(geom, {OVERVIEW_TOLERANCE}) AS _simple "
        f"FROM {view})) WHERE NOT ST_IsEmpty(geom)")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            local = os.path.join(tmp, os.path.basename(path))
            _copy_geoparquet(con, f"{view}_overview", local)
            meta = gcs.upload(local, path, content_type=PARQUET_MIME, cache_control=gcs.CACHE_MUTABLE)
    except Exception:
        gcs.delete(path)   # the old overview is of the old archive; the item must not list it
        raise
    print(f"[{topic.fqn}] overview: {config.public_url(path)}")
    return meta


def write(topic: Topic, con: duckdb.DuckDBPyConnection, view: str) -> gcs.FileMeta:
    """Write `{stem}.parquet` (latest) + dated archive to GCS. DuckDB streams the COPY (with the
    global hilbert sort) under the memory cap → bounded memory regardless of table size."""
    with tempfile.TemporaryDirectory() as tmp:
        local = os.path.join(tmp, f"{topic.stem}.parquet")
        _copy_geoparquet(con, view, local)
        return _upload(topic, local)
