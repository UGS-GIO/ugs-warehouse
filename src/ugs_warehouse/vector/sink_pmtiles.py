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


def _write_geojsonl(con: duckdb.DuckDBPyConnection, view: str, path: str) -> None:
    """COPY the transformed `view` to a GeoJSONSeq file (DuckDB streams it)."""
    con.execute(f"COPY (SELECT * FROM {view}) TO '{path}' (FORMAT GDAL, DRIVER 'GeoJSONSeq')")


def _tile_and_upload(topic: Topic, geojsonl: str) -> None:
    """tippecanoe over a GeoJSONSeq file → PMTiles → GCS (latest pointer). Output sits next to
    the input so callers control the temp dir."""
    if not shutil.which(TIPPECANOE_BIN):
        raise RuntimeError(
            f"{TIPPECANOE_BIN} not on PATH — install tippecanoe in the runtime image"
        )
    pmtiles = os.path.join(os.path.dirname(geojsonl), f"{topic.stem}.pmtiles")
    cmd = [
        TIPPECANOE_BIN,
        "-o", pmtiles,
        "-l", topic.stem,
        "--force",
        # -r1: keep EVERY point at every zoom. Tippecanoe's default drop-rate (2.5) thins dense
        # points at low/mid zoom — point layers (mt stations, wells) rendered ~1 dot until z12+.
        # Lines/polys don't rate-drop, so they looked fine. --drop-densest stays a size-only
        # safety valve (with -r1 it rarely trips at UGS scale).
        "-r1",
        "--drop-densest-as-needed",
        "--extend-zooms-if-still-dropping",
        *EXTRA_OPTS,
        geojsonl,
    ]
    subprocess.run(cmd, check=True)
    gcs_object = f"{config.PMTILES_PREFIX}/{topic.stem}/{topic.stem}.pmtiles"
    # pmtiles is a "latest" pointer, overwritten each ingest -> revalidate via CDN.
    gcs.upload(pmtiles, gcs_object, content_type=PMTILES_MIME, cache_control=gcs.CACHE_MUTABLE)
    print(f"[{topic.fqn}] pmtiles: {config.public_url(gcs_object)}")


def build(topic: Topic, con: duckdb.DuckDBPyConnection, view: str) -> None:
    """Build + upload PMTiles for the transformed view. DuckDB streams the GeoJSONSeq export and
    tippecanoe streams its input → bounded memory regardless of table size."""
    with tempfile.TemporaryDirectory() as tmp:
        geojsonl = os.path.join(tmp, f"{topic.stem}.geojsonl")
        _write_geojsonl(con, view, geojsonl)
        _tile_and_upload(topic, geojsonl)
