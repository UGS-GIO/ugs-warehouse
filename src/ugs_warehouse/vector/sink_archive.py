"""Write the transformed topic to GeoParquet on GCS — native geometry, citable.

Two artifacts per ingest:
  {ARCHIVE_PREFIX}/{stem}/{stem}.parquet               (latest pointer, overwritten -> no-cache)
  {ARCHIVE_PREFIX}/{stem}/{stem}_{YYYYMMDD}.parquet    (dated archive, immutable -> long cache)

The latest pointer is the easy-to-link copy; dated archives are the citable snapshots.
GCS IO + bucket/prefix/CDN come from `core` (shared with the pubs producer).
"""
from __future__ import annotations

import datetime
import os
import tempfile

import duckdb

from ..core import config, gcs
from .topics import Topic

PARQUET_MIME = config.PARQUET_MIME

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


def _copy_geoparquet(con: duckdb.DuckDBPyConnection, view: str, path: str) -> None:
    """COPY the transformed `view` to a GeoParquet file.

    DuckDB's spatial extension auto-writes GeoParquet metadata (version 1.0.0) when a GEOMETRY
    column is present. For spatial pruning we add the per-row extent as four plain numeric columns
    (bbox_xmin/ymin/xmax/ymax); combined with the hilbert ordering (transform), their per-row-group
    min/max stats let our consumers (DuckDB, DuckDB-WASM, OGC API) prune row groups on a bbox
    without decoding geometry.

    NOTE: these are plain columns, NOT the standardized GeoParquet 1.1 `covering` bbox struct.
    DuckDB doesn't emit `covering` at any version (still unimplemented upstream), and the ingest is
    deliberately pyarrow-free + memory-bounded (no post-process rewrite). So spec-aware external
    readers (GDAL/pyarrow) won't auto-detect the bbox column — pushdown still works for our consumers
    via row-group stats. For strict 1.1 covering, post-process with `gpio convert` (geoparquet-io).
    """
    rows_per_group = _row_group_size(con, view)
    print(f"[archive] row group size: {rows_per_group} rows (~{TARGET_ROW_GROUP_BYTES // 1024**2} MB)")
    con.execute(
        f"COPY (SELECT *, "
        f"ST_XMin(geom) AS bbox_xmin, ST_YMin(geom) AS bbox_ymin, "
        f"ST_XMax(geom) AS bbox_xmax, ST_YMax(geom) AS bbox_ymax "
        f"FROM {view}) TO '{path}' "
        f"(FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE {rows_per_group})"
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


def write(topic: Topic, con: duckdb.DuckDBPyConnection, view: str) -> gcs.FileMeta:
    """Write `{stem}.parquet` (latest) + dated archive to GCS. DuckDB streams the COPY (with the
    global hilbert sort) under the memory cap → bounded memory regardless of table size."""
    with tempfile.TemporaryDirectory() as tmp:
        local = os.path.join(tmp, f"{topic.stem}.parquet")
        _copy_geoparquet(con, view, local)
        return _upload(topic, local)
