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

# A second tile layer, `<stem>_labels`, holds one point per polygon at its pole of inaccessibility.
# MapLibre labels a polygon once per tile it crosses, so a style that labels the polygons themselves
# repeats names along tile edges; labelling from this layer gives one label per feature.
# Only up to LABELS_MAX_POLYGONS: -r1 keeps every point at every zoom, and on enmin_plss_sections
# (84,756 polygons) the layer added 96 MB to a 119 MB archive and 2 minutes to the build.
LABELS_SUFFIX = "_labels"
LABELS_MAX_POLYGONS = 5_000
POLYGON_WHERE = "ST_Dimension(geom) = 2 AND NOT ST_IsEmpty(geom)"

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
    # NOTE: --use-attribute-for-id=feature_id is appended per-topic via run_tippecanoe, since it
    # takes the input's id attr; it is NOT a fixed option. Always feature_id, never ugs_key: see
    # tiling_signature for why.
]


# Extra flags for the unit polygons under the maps (pubs/unit_polys.py, pubs/identify.py): a small
# unit polygon stays a polygon at every zoom instead of being folded into its neighbours, since a
# click at any zoom must find the unit under it. TILE_OPTS already lift tippecanoe's per-tile
# feature and size limits and its drop rate, so no polygon is dropped.
IDENTIFY_OPTS = ["--no-tiny-polygon-reduction"]


def tiling_signature(has_ugs_key: bool, has_labels: bool = False) -> str:
    """Stable string of the tiling inputs (build version + whether ugs_key rides as a tile property
    + fixed flags + env opts). Folded into the content fingerprint so a change in HOW a topic is
    tiled forces a rebuild even when the data is byte-identical.

    The MVT feature id is always `feature_id` — the viewer joins a clicked map feature to its table
    row on it and MapLibre exposes it as `feature.id`. `ugs_key` is deliberately NOT promoted to the
    id: tippecanoe's `--use-attribute-for-id` *moves* the attribute out of tile `properties`, which
    would strip `ugs_key` from the properties the viewer's ugs_key-keyed row-commenting reads.
    Instead `ugs_key` stays a plain tile property on armed topics, and `has_ugs_key` is folded in
    here so a dormant→armed topic re-tiles exactly once to pick that property up. `has_labels` is
    folded in only when true, so a topic gaining a labels layer re-tiles and no other signature
    moves. `--force` is excluded — it's not a tiling-output input.

    Blast radius: this component is new to the content fingerprint, so the first `--all` run after
    this ships re-tiles the whole catalog once (enmin_plss_sections' 84k features included);
    thereafter a topic re-tiles only when its data changes or it arms. `PMTILES_BUILD_VERSION` is the
    knob for any future forced whole-catalog rebuild."""
    opts = [o for o in (*TILE_OPTS, *EXTRA_OPTS) if o != "--force"]
    return "|".join([f"v{PMTILES_BUILD_VERSION}", f"ugskey={int(has_ugs_key)}",
                     *(["labels=1"] if has_labels else []), *opts])


def has_labels(con: duckdb.DuckDBPyConnection, view: str) -> bool:
    """Whether `build` writes a labels layer: the view has 1 to LABELS_MAX_POLYGONS polygons."""
    n = con.execute(f"SELECT count(*) FROM (SELECT 1 FROM {view} WHERE {POLYGON_WHERE} "
                    f"LIMIT {LABELS_MAX_POLYGONS + 1})").fetchone()[0]
    return 0 < n <= LABELS_MAX_POLYGONS


def tile_layers(topic: Topic, con: duckdb.DuckDBPyConnection, view: str) -> list[str]:
    """The tile layers `build` writes, in order: the topic's own layer first (consumers read
    `pmtiles:layers[0]`), then the labels layer when `has_labels`."""
    return [topic.stem, *([f"{topic.stem}{LABELS_SUFFIX}"] if has_labels(con, view) else [])]


def write_label_geojsonl(con: duckdb.DuckDBPyConnection, view: str, path: str) -> None:
    """Each polygon row of `view` as a point at its pole of inaccessibility, every attribute kept, to
    a GeoJSONSeq file. Unlike a centroid the point is always inside, even for a concave county."""
    con.execute(
        f"COPY (SELECT * REPLACE ((ST_MaximumInscribedCircle(geom)).center AS geom) FROM {view} "
        f"WHERE {POLYGON_WHERE}) TO '{path}' (FORMAT GDAL, DRIVER 'GeoJSONSeq')"
    )


def write_geojsonl(con: duckdb.DuckDBPyConnection, view: str, path: str) -> None:
    """COPY `view` (its geometry column a DuckDB GEOMETRY in lon/lat) to a GeoJSONSeq file. DuckDB
    streams the export; the pubs producer calls this too."""
    con.execute(f"COPY (SELECT * FROM {view}) TO '{path}' (FORMAT GDAL, DRIVER 'GeoJSONSeq')")


def run_tippecanoe(geojsonl: str, pmtiles: str, layer: str, *, id_attr: str | None = None,
                   what: str | None = None, extra_opts: list[str] | None = None,
                   extra_layers: list[tuple[str, str]] | None = None) -> None:
    """tippecanoe over a GeoJSONSeq file into `pmtiles`, one tile layer named `layer`, with the fixed
    TILE_OPTS + `extra_opts` + TIPPECANOE_OPTS. `extra_layers` are more (name, GeoJSONSeq) layers in
    the same archive. `id_attr` is promoted to the MVT feature id. Raises RuntimeError
    carrying tippecanoe's stderr, naming `what` (default `layer`)."""
    if not shutil.which(TIPPECANOE_BIN):
        raise RuntimeError(
            f"{TIPPECANOE_BIN} not on PATH — install tippecanoe in the runtime image"
        )
    # -l would merge every input into one layer, so several layers each get a named -L input.
    named = [arg for name, path in [(layer, geojsonl), *(extra_layers or [])]
             for arg in ("-L", f"{name}:{path}")]
    cmd = [
        TIPPECANOE_BIN,
        "-o", pmtiles,
        *(["-l", layer] if not extra_layers else []),
        *TILE_OPTS,
        *(extra_opts or []),
        *([f"--use-attribute-for-id={id_attr}"] if id_attr else []),
        *EXTRA_OPTS,
        *([geojsonl] if not extra_layers else named),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        # check=True alone gives a bare "exit status N"; the stderr has the reason (bad geometry,
        # id collisions, ...), so callers that log str(e) show the root cause.
        detail = (e.stderr or e.stdout or "").strip()
        raise RuntimeError(
            f"tippecanoe exited {e.returncode} for {what or layer}"
            + (f":\n{detail}" if detail else "")
        ) from e


def _tile_and_upload(topic: Topic, geojsonl: str, id_attr: str,
                     extra_layers: list[tuple[str, str]] | None = None) -> gcs.FileMeta:
    """tippecanoe over a GeoJSONSeq file → PMTiles → GCS (latest pointer). Output sits next to the
    input so callers control the temp dir. `id_attr` (always `feature_id`) is promoted to the native
    MVT feature id so the viewer can join a clicked map feature to its GeoParquet table row: both
    carry the same id, and MapLibre exposes it as `feature.id`."""
    pmtiles = os.path.join(os.path.dirname(geojsonl), f"{topic.stem}.pmtiles")
    run_tippecanoe(geojsonl, pmtiles, topic.stem, id_attr=id_attr, what=topic.fqn,
                   extra_layers=extra_layers)
    gcs_object = f"{config.PMTILES_PREFIX}/{topic.stem}/{topic.stem}.pmtiles"
    # pmtiles is a "latest" pointer, overwritten each ingest -> revalidate via CDN.
    meta = gcs.upload(pmtiles, gcs_object, content_type=PMTILES_MIME, cache_control=gcs.CACHE_MUTABLE)
    print(f"[{topic.fqn}] pmtiles: {config.public_url(gcs_object)}")
    return meta


def build(topic: Topic, con: duckdb.DuckDBPyConnection, view: str) -> gcs.FileMeta:
    """Build + upload PMTiles for the transformed view. DuckDB streams the GeoJSONSeq export and
    tippecanoe streams its input → bounded memory regardless of table size."""
    with tempfile.TemporaryDirectory() as tmp:
        geojsonl = os.path.join(tmp, f"{topic.stem}.geojsonl")
        write_geojsonl(con, view, geojsonl)
        extra_layers = []
        if has_labels(con, view):
            labels = os.path.join(tmp, f"{topic.stem}{LABELS_SUFFIX}.geojsonl")
            write_label_geojsonl(con, view, labels)
            extra_layers.append((f"{topic.stem}{LABELS_SUFFIX}", labels))
        # MVT id is always feature_id (viewer map→table join); ugs_key rides along as a property.
        return _tile_and_upload(topic, geojsonl, introspect.FEATURE_ID, extra_layers)
