"""DuckDB-side transform: confirm CRS = 4326, add h3_r9, hilbert-sort.

`materialize` turns a source relation carrying `geom_wkb` (BLOB) + `target_epsg` into a DuckDB
table in EPSG:4326 with:
  - `geom`  GEOMETRY (4326), hydrated from `geom_wkb`
  - `h3_r9` UBIGINT, H3 cell at resolution 9 from the centroid
  - rows ORDER BY `ST_Hilbert(centroid)` so parquet row-groups bbox-prune well

The source relation is a postgres_query subquery (see `source.stream_transformed`) — everything
stays inside DuckDB, no pyarrow. Sinks then read the materialized table.
"""
from __future__ import annotations

import os

import duckdb

H3_RESOLUTION = 9
TARGET_SRS = 4326

# Free-tier OOM guard: DuckDB spills to disk past this. Tunable now that pyarrow no longer
# loads on the streaming path — raise it for more in-RAM sort (faster, less spill) if headroom.
MAX_MEMORY = os.environ.get("DUCKDB_MAX_MEMORY", "128MB")


def setup(con: duckdb.DuckDBPyConnection) -> None:
    """Cap memory (free-tier OOM guard → DuckDB spills to disk) + load spatial/h3.

    LOAD first: the Docker image pre-bakes both extensions, so the common path is a local load
    with NO network call to the extension servers. INSTALL is the fallback for an un-baked env
    (local dev) — `h3` is a community extension, hence `FROM community`.
    """
    con.execute(f"SET max_memory='{MAX_MEMORY}';")
    for ext, source in (("spatial", ""), ("h3", " FROM community")):
        try:
            con.execute(f"LOAD {ext};")
        except duckdb.Error:
            con.execute(f"INSTALL {ext}{source}; LOAD {ext};")


def _select(source_rel: str) -> str:
    """The transform SELECT over a relation carrying business cols + `geom_wkb` + `target_epsg`.

    If target_epsg is already 4326, just hydrate the WKB; else (transitional) reproject from
    target_epsg → 4326 (the storage CRS the WKB is in, NOT the provenance source_epsg). DuckDB
    GEOMETRY carries no SRID — CRS is attached at GeoParquet write time. Rows are hilbert-sorted
    so parquet row-groups bbox-prune well.
    """
    geom_hydrate = (
        f"CASE WHEN target_epsg = {TARGET_SRS} "
        f"  THEN ST_GeomFromWKB(geom_wkb) "
        f"  ELSE ST_Transform(ST_GeomFromWKB(geom_wkb), 'EPSG:' || target_epsg, "
        f"    'EPSG:{TARGET_SRS}', always_xy := true) "
        f"END"
    )
    return f"""
        WITH hydrated AS (
          SELECT * EXCLUDE (geom_wkb), {geom_hydrate} AS geom FROM {source_rel}
        )
        SELECT *, h3_latlng_to_cell(ST_Y(ST_Centroid(geom)), ST_X(ST_Centroid(geom)),
                                    {H3_RESOLUTION}) AS h3_r9
        FROM hydrated
        ORDER BY ST_Hilbert(ST_Centroid(geom))
    """


def materialize(con: duckdb.DuckDBPyConnection, source_rel: str,
                name: str = "transformed") -> str:
    """Materialize the transform ONCE into a DuckDB table (global hilbert sort; spills under the
    memory cap). Sinks then read the table without recomputing the scan/transform/sort per sink.
    `con` must already have `setup()` run. Returns the table name."""
    con.execute(f"CREATE OR REPLACE TABLE {name} AS {_select(source_rel)}")
    return name
