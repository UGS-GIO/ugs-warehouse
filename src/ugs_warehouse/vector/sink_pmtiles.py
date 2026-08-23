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
from . import introspect
from .topics import Topic

TIPPECANOE_BIN = os.environ.get("TIPPECANOE_BIN", "tippecanoe")
EXTRA_OPTS = shlex.split(os.environ.get("TIPPECANOE_OPTS", ""))
PMTILES_MIME = config.PMTILES_MIME

# Bump when the tiling logic below changes in a way that should force a rebuild of unchanged
# topics (new flag, different feature-id handling, etc.). Folded into the content fingerprint.
PMTILES_BUILD_VERSION = 2

# Fixed tippecanoe flags (everything but -o/-l/the input). Hoisted so the build command and the
# tiling fingerprint share ONE source of truth — see _tile_and_upload + tiling_signature.
TILE_OPTS = [
    "--force",
    # -r1: keep EVERY point at every zoom. Tippecanoe's default drop-rate (2.5) thins dense
    # points at low/mid zoom — point layers (mt stations, wells) rendered ~1 dot until z12+.
    "-r1",
    # UGS hazard/geologic layers need every feature present at every zoom — a missing fault
    # segment or unit polygon is a wrong map, not a rendering nicety. Explicitly lift both of
    # tippecanoe's automatic thinning triggers instead of relying on --drop-densest-as-needed
    # "rarely tripping" — it does trip on larger line/poly topics (e.g. debris-flow segments)
    # and silently drops features to fit the 500KB/tile, 200k-feature/tile defaults.
    "--no-tile-size-limit",
    "--no-feature-limit",
    # NOTE: --use-attribute-for-id is appended per-topic in _tile_and_upload (ugs_key on armed
    # topics, else feature_id) — it is NOT a fixed option, so it lives outside TILE_OPTS.
]


def tiling_signature(id_attr: str) -> str:
    """Stable string of the tiling inputs (build version + the MVT id column + fixed flags + env
    opts). Folded into the content fingerprint so a change in HOW a topic is tiled forces a rebuild
    even when the data is byte-identical. Including `id_attr` means a dormant→armed topic (whose id
    source flips feature_id→ugs_key) re-tiles exactly when that happens. `--force` is excluded —
    it's not a tiling-output input."""
    opts = [o for o in (*TILE_OPTS, *EXTRA_OPTS) if o != "--force"]
    return "|".join([f"v{PMTILES_BUILD_VERSION}", f"id={id_attr}", *opts])


def _write_geojsonl(con: duckdb.DuckDBPyConnection, view: str, path: str) -> None:
    """COPY the transformed `view` to a GeoJSONSeq file (DuckDB streams it)."""
    con.execute(f"COPY (SELECT * FROM {view}) TO '{path}' (FORMAT GDAL, DRIVER 'GeoJSONSeq')")


def _tile_and_upload(topic: Topic, geojsonl: str, id_attr: str) -> None:
    """tippecanoe over a GeoJSONSeq file → PMTiles → GCS (latest pointer). Output sits next to
    the input so callers control the temp dir. `id_attr` is promoted to the native MVT feature id
    (ugs_key on armed topics, else feature_id) so the viewer can join a clicked map feature to its
    GeoParquet table row — both carry the same id; MapLibre exposes it as `feature.id`."""
    if not shutil.which(TIPPECANOE_BIN):
        raise RuntimeError(
            f"{TIPPECANOE_BIN} not on PATH — install tippecanoe in the runtime image"
        )
    pmtiles = os.path.join(os.path.dirname(geojsonl), f"{topic.stem}.pmtiles")
    cmd = [
        TIPPECANOE_BIN,
        "-o", pmtiles,
        "-l", topic.stem,
        *TILE_OPTS,
        f"--use-attribute-for-id={id_attr}",
        *EXTRA_OPTS,
        geojsonl,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        # tippecanoe's stderr has the actual reason (bad geometry, id collisions, etc).
        # check=True alone only gives a bare "exit status N" — fold the real message in so
        # ingest.py's per-sink FAILED log (which prints str(e)) shows root cause, not just rc.
        detail = (e.stderr or e.stdout or "").strip()
        raise RuntimeError(
            f"tippecanoe exited {e.returncode} for {topic.fqn}"
            + (f":\n{detail}" if detail else "")
        ) from e
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
        _tile_and_upload(topic, geojsonl, introspect.id_column(con, view))
