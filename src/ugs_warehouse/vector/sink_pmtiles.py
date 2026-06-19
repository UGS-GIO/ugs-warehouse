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


def write_chunk(con: duckdb.DuckDBPyConnection, view: str, path: str) -> None:
    """COPY the transformed `view` to one GeoJSONSeq file (used per-chunk by chunked ingest)."""
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
    """Build + upload PMTiles for the whole transformed view (single-shot, non-chunked)."""
    with tempfile.TemporaryDirectory() as tmp:
        geojsonl = os.path.join(tmp, f"{topic.stem}.geojsonl")
        write_chunk(con, view, geojsonl)
        _tile_and_upload(topic, geojsonl)


def finalize(topic: Topic, chunks_dir: str) -> None:
    """Concatenate per-chunk GeoJSONSeq files (chunked ingest) → tippecanoe → upload. Concat is
    a streaming file copy (no rows held in memory); tippecanoe streams its input."""
    import glob

    chunks = sorted(
        glob.glob(os.path.join(chunks_dir, "*.geojsonl")),
        key=lambda p: int(os.path.basename(p).split("_")[1].split(".")[0]),
    )
    if not chunks:
        raise RuntimeError("no geojsonl chunks to finalize")
    with tempfile.TemporaryDirectory() as tmp:
        combined = os.path.join(tmp, f"{topic.stem}.geojsonl")
        with open(combined, "wb") as out:
            for c in chunks:
                with open(c, "rb") as f:
                    shutil.copyfileobj(f, out)
        _tile_and_upload(topic, combined)
