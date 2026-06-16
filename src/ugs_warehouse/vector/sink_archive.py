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

PARQUET_MIME = "application/vnd.apache.parquet"


def write(topic: Topic, con: duckdb.DuckDBPyConnection, view: str) -> None:
    """Write `{stem}.parquet` (latest) + dated archive to GCS."""
    stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d")
    with tempfile.TemporaryDirectory() as tmp:
        local = os.path.join(tmp, f"{topic.stem}.parquet")
        # DuckDB's spatial extension auto-writes GeoParquet metadata when a GEOMETRY
        # column is present and FORMAT PARQUET is requested.
        #
        # bbox covering: per-row geometry extent as four plain numeric columns. Combined with
        # the hilbert ordering (transform), their per-row-group min/max stats let any reader
        # (DuckDB, DuckDB-WASM in the viewer, a future OGC API) prune row groups on a bbox
        # without decoding geometry. Cheap; helps analytics + client-side + serving alike.
        con.execute(
            f"COPY (SELECT *, "
            f"ST_XMin(geom) AS bbox_xmin, ST_YMin(geom) AS bbox_ymin, "
            f"ST_XMax(geom) AS bbox_xmax, ST_YMax(geom) AS bbox_ymax "
            f"FROM {view}) TO '{local}' (FORMAT PARQUET, COMPRESSION ZSTD)"
        )
        base = f"{config.ARCHIVE_PREFIX}/{topic.stem}"
        latest = f"{base}/{topic.stem}.parquet"
        dated = f"{base}/{topic.stem}_{stamp}.parquet"
        gcs.upload(local, latest, content_type=PARQUET_MIME, cache_control=gcs.CACHE_MUTABLE)
        gcs.upload(local, dated, content_type=PARQUET_MIME, cache_control=gcs.CACHE_IMMUTABLE)

    print(f"[{topic.fqn}] archive: {config.public_url(latest)} (+ dated {stamp})")
