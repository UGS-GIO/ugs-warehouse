"""DuckDB-side transform: confirm CRS = 4326, add h3_r9, hilbert-sort.

Input: a pyarrow Table from `source.read` carrying `geom_wkb` (BLOB) plus the
dbt-mart `source_epsg` / `target_epsg` columns.

Output: a DuckDB connection holding a `transformed` view in EPSG:4326 with:
  - `geom`  GEOMETRY (4326), hydrated from `geom_wkb`
  - `h3_r9` UBIGINT, H3 cell at resolution 9 from the centroid
  - rows ORDER BY `ST_Hilbert(centroid)` so parquet row-groups bbox-prune well

Each sink reads from the view in the form it needs:
  - sink_iceberg: SELECT * REPLACE (ST_AsWKB(geom) AS geom) -> BLOB column
  - sink_archive: COPY ... TO 'gs://.parquet' (FORMAT PARQUET) -> GeoParquet
"""
from __future__ import annotations

import duckdb
import pyarrow as pa

H3_RESOLUTION = 9
TARGET_SRS = 4326


def run(arrow_in: pa.Table) -> tuple[duckdb.DuckDBPyConnection, str]:
    """Build the `transformed` view; return (connection, view_name)."""
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial;")
    try:
        con.execute("INSTALL h3 FROM community; LOAD h3;")
    except duckdb.Error:
        con.execute("LOAD h3;")

    con.register("source_arrow", arrow_in)

    # If target_epsg is already 4326, just hydrate the WKB.
    # If not (transitional pre-cutover), reproject from target_epsg -> 4326.
    # Reproject source is target_epsg (the storage CRS the WKB is in), NOT
    # source_epsg (the upstream's original CRS, kept only for provenance).
    # Wrap in ST_SetSRID(.., 4326) so the resulting GEOMETRY carries the
    # correct SRID for downstream sinks (esp. GeoParquet metadata).
    geom_hydrate = (
        f"ST_SetSRID("
        f"  CASE WHEN target_epsg = {TARGET_SRS} "
        f"  THEN ST_GeomFromWKB(geom_wkb) "
        f"  ELSE ST_Transform("
        f"    ST_GeomFromWKB(geom_wkb), "
        f"    'EPSG:' || target_epsg, "
        f"    'EPSG:{TARGET_SRS}', "
        f"    always_xy := true) "
        f"  END,"
        f"  {TARGET_SRS}"
        f")"
    )

    con.execute(f"""
        CREATE VIEW transformed AS
        WITH hydrated AS (
          SELECT
            * EXCLUDE (geom_wkb),
            {geom_hydrate} AS geom
          FROM source_arrow
        )
        SELECT
          *,
          h3_latlng_to_cell(
            ST_Y(ST_Centroid(geom)),
            ST_X(ST_Centroid(geom)),
            {H3_RESOLUTION}
          ) AS h3_r9
        FROM hydrated
        ORDER BY ST_Hilbert(ST_Centroid(geom))
    """)
    return con, "transformed"
