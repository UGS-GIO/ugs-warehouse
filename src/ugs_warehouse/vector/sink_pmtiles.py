"""Build per-topic PMTiles via tippecanoe and upload to GCS.

Pipeline (all in a temp dir):
  DuckDB transformed view -> local NDJSON GeoJSON (via spatial GDAL driver)
  -> tippecanoe -> local .pmtiles -> GCS

External binary required: tippecanoe (installed in the Cloud Run runtime image).

Env:
  TIPPECANOE_BIN            default tippecanoe
  TIPPECANOE_OPTS           extra flags appended to defaults (space-separated)
GCS IO + prefix/CDN come from `core` (shared with the pubs producer).
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import tempfile

import duckdb

from ..core import config, gcs
from .topics import Topic

TIPPECANOE_BIN = os.environ.get("TIPPECANOE_BIN", "tippecanoe")
EXTRA_OPTS = shlex.split(os.environ.get("TIPPECANOE_OPTS", ""))
PMTILES_MIME = "application/vnd.pmtiles"


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
        gcs_object = f"{config.PMTILES_PREFIX}/{topic.stem}/{topic.stem}.pmtiles"
        # pmtiles is a "latest" pointer, overwritten each ingest -> revalidate via CDN.
        gcs.upload(pmtiles, gcs_object, content_type=PMTILES_MIME,
                   cache_control=gcs.CACHE_MUTABLE)

    print(f"[{topic.fqn}] pmtiles: {config.public_url(gcs_object)}")
