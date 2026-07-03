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
    con.execute(
        f"COPY (SELECT *, "
        f"ST_XMin(geom) AS bbox_xmin, ST_YMin(geom) AS bbox_ymin, "
        f"ST_XMax(geom) AS bbox_xmax, ST_YMax(geom) AS bbox_ymax "
        f"FROM {view}) TO '{path}' (FORMAT PARQUET, COMPRESSION ZSTD)"
    )


def _upload(topic: Topic, local: str) -> None:
    """Upload a finished GeoParquet as the latest pointer + a dated immutable snapshot."""
    stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d")
    base = f"{config.ARCHIVE_PREFIX}/{topic.stem}"
    latest = f"{base}/{topic.stem}.parquet"
    dated = f"{base}/{topic.stem}_{stamp}.parquet"
    gcs.upload(local, latest, content_type=PARQUET_MIME, cache_control=gcs.CACHE_MUTABLE)
    gcs.upload(local, dated, content_type=PARQUET_MIME, cache_control=gcs.CACHE_IMMUTABLE)
    print(f"[{topic.fqn}] archive: {config.public_url(latest)} (+ dated {stamp})")


def write(topic: Topic, con: duckdb.DuckDBPyConnection, view: str) -> None:
    """Write `{stem}.parquet` (latest) + dated archive to GCS. DuckDB streams the COPY (with the
    global hilbert sort) under the memory cap → bounded memory regardless of table size."""
    with tempfile.TemporaryDirectory() as tmp:
        local = os.path.join(tmp, f"{topic.stem}.parquet")
        _copy_geoparquet(con, view, local)
        _upload(topic, local)
