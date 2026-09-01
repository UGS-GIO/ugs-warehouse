"""DuckDB-side transform: confirm CRS = 4326, hilbert-sort.

`materialize` turns a source relation carrying `geom_wkb` (BLOB) + `target_epsg` into a DuckDB
table in EPSG:4326 with:
  - `geom`  GEOMETRY (4326), hydrated from `geom_wkb`
  - rows ORDER BY `ST_Hilbert(centroid)` so parquet row-groups bbox-prune well (this hilbert
    locality is also our spatial index — no separate hex/grid column until a consumer needs one)

The source relation is a postgres_query subquery (see `source.stream_transformed`) — everything
stays inside DuckDB, no pyarrow. Sinks then read the materialized table.
"""
from __future__ import annotations

import os

import duckdb

TARGET_SRS = 4326

# Hard ceiling — the global sort in _select does NOT spill. Size to ~2x the largest table.
MAX_MEMORY = os.environ.get("DUCKDB_MAX_MEMORY", "128MB")


def setup(con: duckdb.DuckDBPyConnection) -> None:
    """Cap memory (MAX_MEMORY — a hard ceiling; the sort does not spill) + load spatial.

    LOAD first: the Docker image pre-bakes the extension, so the common path is a local load with
    NO network call to the extension servers. INSTALL is the fallback for an un-baked env (local dev).
    """
    con.execute(f"SET max_memory='{MAX_MEMORY}';")
    try:
        con.execute("LOAD spatial;")
    except duckdb.Error:
        con.execute("INSTALL spatial; LOAD spatial;")


def _select(source_rel: str) -> str:
    """The transform SELECT over a relation carrying business cols + `geom_wkb` + `target_epsg`.

    If target_epsg is already 4326, just hydrate the WKB; else (transitional) reproject from
    target_epsg → 4326 (the storage CRS the WKB is in, NOT the provenance source_epsg). DuckDB
    GEOMETRY carries no SRID — CRS is attached at GeoParquet write time. Rows are hilbert-sorted
    so parquet row-groups bbox-prune well.

    Each row also gets a stable `feature_id` (1..N in hilbert order). Because the GeoParquet and
    the PMTiles sinks both read this one materialized table, the id is identical in both — it's
    the join key the viewer uses to link a clicked map feature to its table row (and back).

    The hilbert order is tie-broken by `hash(row)` so feature_id is fully deterministic — identical
    source → identical ids, run to run. Without the tiebreak, co-located rows (same centroid hilbert
    value) get an arbitrary `row_number()` order that can shuffle between runs; that makes the
    content fingerprint (skip-unchanged ingest, see fingerprint.py) unstable, so unchanged topics
    would rebuild every time. The tiebreak is what makes feature_id a cross-ingest-stable key.

    target_epsg = 0 (unstamped geometry) errors loudly rather than silently assuming 4326 — an
    unstamped table reaching here means a CRS provenance gap upstream, not a 4326 default.
    """
    geom_hydrate = (
        f"CASE WHEN target_epsg = 0 "
        f"  THEN error('source geometry has SRID 0 (unstamped CRS) — refusing to assume "
        f"EPSG:{TARGET_SRS}; stamp the table SRID upstream') "
        f"WHEN target_epsg = {TARGET_SRS} "
        f"  THEN ST_GeomFromWKB(geom_wkb) "
        f"  ELSE ST_Transform(ST_GeomFromWKB(geom_wkb), 'EPSG:' || target_epsg, "
        f"    'EPSG:{TARGET_SRS}', always_xy := true) "
        f"END"
    )
    return f"""
        WITH hydrated AS (
          SELECT * EXCLUDE (geom_wkb), {geom_hydrate} AS geom FROM {source_rel}
        )
        SELECT *,
               row_number() OVER (ORDER BY ST_Hilbert(ST_Centroid(geom)), hash(hydrated))
                 AS feature_id
        FROM hydrated
        ORDER BY ST_Hilbert(ST_Centroid(geom)), hash(hydrated)
    """


def materialize(con: duckdb.DuckDBPyConnection, source_rel: str,
                name: str = "transformed") -> str:
    """Materialize the transform ONCE into a DuckDB table (global hilbert sort; OOMs past
    MAX_MEMORY rather than spilling). Sinks then read it without recomputing the transform.
    `con` must already have `setup()` run. Returns the table name."""
    con.execute(f"CREATE OR REPLACE TABLE {name} AS {_select(source_rel)}")
    return name
