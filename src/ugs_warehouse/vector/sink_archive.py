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


def write_chunk(con: duckdb.DuckDBPyConnection, view: str, path: str) -> None:
    """COPY the transformed `view` to one GeoParquet file (used per-chunk by chunked ingest).

    DuckDB's spatial extension auto-writes GeoParquet metadata when a GEOMETRY column is present.
    bbox covering: per-row extent as four plain numeric columns — combined with the hilbert
    ordering (transform), their per-row-group min/max stats let any reader (DuckDB, DuckDB-WASM,
    OGC API) prune row groups on a bbox without decoding geometry.
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
    """Write `{stem}.parquet` (latest) + dated archive to GCS (single-shot, non-chunked)."""
    with tempfile.TemporaryDirectory() as tmp:
        local = os.path.join(tmp, f"{topic.stem}.parquet")
        write_chunk(con, view, local)
        _upload(topic, local)


def finalize(topic: Topic, chunks_dir: str) -> None:
    """Merge per-chunk GeoParquet files (chunked ingest) into one archive + upload. The merge
    streams via `read_parquet` under a 128MB cap — bounded memory regardless of total size."""
    import glob

    import duckdb as _ddb

    from . import transform
    if not glob.glob(os.path.join(chunks_dir, "*.parquet")):
        raise RuntimeError("no parquet chunks to finalize")
    with tempfile.TemporaryDirectory() as tmp:
        local = os.path.join(tmp, f"{topic.stem}.parquet")
        merge = _ddb.connect()
        merge.execute(f"SET max_memory='{transform.MAX_MEMORY}';")
        merge.execute("INSTALL spatial; LOAD spatial;")
        try:
            merge.execute(
                f"COPY (SELECT * FROM read_parquet('{chunks_dir}/*.parquet')) "
                f"TO '{local}' (FORMAT PARQUET, COMPRESSION ZSTD)"
            )
        finally:
            merge.close()
        _upload(topic, local)
