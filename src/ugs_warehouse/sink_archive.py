"""Write the transformed topic to GeoParquet on GCS — native geometry, citable.

Two artifacts per ingest:
  gs://{BUCKET}/{PREFIX}/{topic_stem}/{topic_stem}.parquet              (latest pointer, overwritten)
  gs://{BUCKET}/{PREFIX}/{topic_stem}/{topic_stem}_{YYYYMMDD}.parquet   (dated archive, immutable)

The latest pointer is the easy-to-link copy; dated archives are what STAC
items reference for citable snapshots.

Env:
  WAREHOUSE_ARCHIVE_BUCKET  default ut-dnr-ugs-maps-prod-public
  WAREHOUSE_ARCHIVE_PREFIX  default warehouse/geoparquet
"""
from __future__ import annotations

import datetime
import os
import tempfile

import duckdb
from google.cloud import storage

from .topics import Topic

ARCHIVE_BUCKET = os.environ.get("WAREHOUSE_ARCHIVE_BUCKET", "ut-dnr-ugs-maps-prod-public")
ARCHIVE_PREFIX = os.environ.get("WAREHOUSE_ARCHIVE_PREFIX", "warehouse/geoparquet")
PARQUET_MIME = "application/vnd.apache.parquet"


def _upload(local_path: str, gcs_object: str) -> None:
    bucket = storage.Client().bucket(ARCHIVE_BUCKET)
    bucket.blob(gcs_object).upload_from_filename(local_path, content_type=PARQUET_MIME)


def write(topic: Topic, con: duckdb.DuckDBPyConnection, view: str) -> None:
    """Write `{topic_stem}.parquet` (latest) + dated archive to GCS."""
    stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d")
    with tempfile.TemporaryDirectory() as tmp:
        local = os.path.join(tmp, f"{topic.stem}.parquet")
        # DuckDB's spatial extension auto-writes GeoParquet metadata when a
        # GEOMETRY column is present and FORMAT PARQUET is requested.
        con.execute(
            f"COPY (SELECT * FROM {view}) TO '{local}' "
            f"(FORMAT PARQUET, COMPRESSION ZSTD)"
        )
        latest = f"{ARCHIVE_PREFIX}/{topic.stem}/{topic.stem}.parquet"
        dated = f"{ARCHIVE_PREFIX}/{topic.stem}/{topic.stem}_{stamp}.parquet"
        _upload(local, latest)
        _upload(local, dated)

    print(
        f"[{topic.fqn}] archive: gs://{ARCHIVE_BUCKET}/{latest} "
        f"(+ dated {stamp})"
    )
