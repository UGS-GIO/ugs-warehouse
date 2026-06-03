"""Build per-topic PMTiles via tippecanoe and upload to GCS.

Pipeline (all in a temp dir):
  DuckDB transformed view -> local NDJSON GeoJSON (via spatial GDAL driver)
  -> tippecanoe -> local .pmtiles -> GCS

External binary required: tippecanoe (installed in the Cloud Run runtime image).

Env:
  WAREHOUSE_PMTILES_BUCKET  default ut-dnr-ugs-maps-prod-public
  WAREHOUSE_PMTILES_PREFIX  default warehouse/pmtiles
  TIPPECANOE_BIN            default tippecanoe
  TIPPECANOE_OPTS           extra flags appended to defaults (space-separated)
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import tempfile

import duckdb
from google.cloud import storage

from .topics import Topic

PMTILES_BUCKET = os.environ.get("WAREHOUSE_PMTILES_BUCKET", "ut-dnr-ugs-maps-prod-public")
PMTILES_PREFIX = os.environ.get("WAREHOUSE_PMTILES_PREFIX", "warehouse/pmtiles")
TIPPECANOE_BIN = os.environ.get("TIPPECANOE_BIN", "tippecanoe")
EXTRA_OPTS = shlex.split(os.environ.get("TIPPECANOE_OPTS", ""))
PMTILES_MIME = "application/vnd.pmtiles"


def _upload(local: str, gcs_object: str) -> None:
    bucket = storage.Client().bucket(PMTILES_BUCKET)
    bucket.blob(gcs_object).upload_from_filename(local, content_type=PMTILES_MIME)


def build(topic: Topic, con: duckdb.DuckDBPyConnection, view: str) -> None:
    if not shutil.which(TIPPECANOE_BIN):
        raise RuntimeError(
            f"{TIPPECANOE_BIN} not on PATH — install tippecanoe in the runtime image"
        )
    with tempfile.TemporaryDirectory() as tmp:
        geojsonl = os.path.join(tmp, f"{topic.stem}.geojsonl")
        pmtiles = os.path.join(tmp, f"{topic.stem}.pmtiles")
        con.execute(
            f"COPY (SELECT * FROM {view}) TO '{geojsonl}' "
            f"(FORMAT GDAL, DRIVER 'GeoJSONSeq')"
        )
        cmd = [
            TIPPECANOE_BIN,
            "-o", pmtiles,
            "-l", topic.stem,
            "--force",
            "--drop-densest-as-needed",
            "--extend-zooms-if-still-dropping",
            *EXTRA_OPTS,
            geojsonl,
        ]
        subprocess.run(cmd, check=True)
        gcs_object = f"{PMTILES_PREFIX}/{topic.stem}/{topic.stem}.pmtiles"
        _upload(pmtiles, gcs_object)

    print(f"[{topic.fqn}] pmtiles: gs://{PMTILES_BUCKET}/{gcs_object}")
