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
    # NOTE: --use-attribute-for-id=feature_id is appended per-topic in _tile_and_upload — it takes
    # the input's id attr, so it's NOT a fixed option. Always feature_id, never ugs_key: see
    # tiling_signature for why.
]


def tiling_signature(has_ugs_key: bool) -> str:
    """Stable string of the tiling inputs (build version + whether ugs_key rides as a tile property
    + fixed flags + env opts). Folded into the content fingerprint so a change in HOW a topic is
    tiled forces a rebuild even when the data is byte-identical.

    The MVT feature id is always `feature_id` — the viewer joins a clicked map feature to its table
    row on it and MapLibre exposes it as `feature.id`. `ugs_key` is deliberately NOT promoted to the
    id: tippecanoe's `--use-attribute-for-id` *moves* the attribute out of tile `properties`, which
    would strip `ugs_key` from the properties the viewer's ugs_key-keyed row-commenting reads.
    Instead `ugs_key` stays a plain tile property on armed topics, and `has_ugs_key` is folded in
    here so a dormant→armed topic re-tiles exactly once to pick that property up. `--force` is
    excluded — it's not a tiling-output input.

    Blast radius: this component is new to the content fingerprint, so the first `--all` run after
    this ships re-tiles the whole catalog once (enmin_plss_sections' 84k features included);
    thereafter a topic re-tiles only when its data changes or it arms. `PMTILES_BUILD_VERSION` is the
    knob for any future forced whole-catalog rebuild."""
    opts = [o for o in (*TILE_OPTS, *EXTRA_OPTS) if o != "--force"]
    return "|".join([f"v{PMTILES_BUILD_VERSION}", f"ugskey={int(has_ugs_key)}", *opts])


def _write_geojsonl(con: duckdb.DuckDBPyConnection, view: str, path: str) -> None:
    """COPY the transformed `view` to a GeoJSONSeq file (DuckDB streams it)."""
    con.execute(f"COPY (SELECT * FROM {view}) TO '{path}' (FORMAT GDAL, DRIVER 'GeoJSONSeq')")


def _tile_and_upload(topic: Topic, geojsonl: str, id_attr: str) -> None:
    """tippecanoe over a GeoJSONSeq file → PMTiles → GCS (latest pointer). Output sits next to the
    input so callers control the temp dir. `id_attr` (always `feature_id`) is promoted to the native
    MVT feature id so the viewer can join a clicked map feature to its GeoParquet table row — both
    carry the same id; MapLibre exposes it as `feature.id`. `ugs_key`, when present, is NOT used as
    the id here (see tiling_signature): `--use-attribute-for-id` would MOVE it out of `properties`,
    and the viewer's ugs_key-keyed row-commenting reads it from the tile properties."""
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
        # MVT id is always feature_id (viewer map→table join); ugs_key rides along as a property.
        _tile_and_upload(topic, geojsonl, introspect.FEATURE_ID)
