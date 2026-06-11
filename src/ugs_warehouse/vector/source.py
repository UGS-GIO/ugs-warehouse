"""Read `{schema}.{topic}_current` from Postgres into a pyarrow Table.

PostGIS geometry is returned as `geom_wkb` (BLOB) via server-side `ST_AsBinary`;
`transform.py` hydrates it back to a DuckDB GEOMETRY. The dbt-mart `source_epsg`
and `target_epsg` columns travel with each row so the transform knows whether
to confirm or reproject.

Env:
  POSTGRES_DSN  libpq-style DSN.
                Local dev: `cloud_sql_proxy` on localhost.
                Prod (Cloud Run): Cloud SQL Connector instance DSN.
"""
from __future__ import annotations

import os

import duckdb
import pyarrow as pa

from .topics import MART_SCHEMAS, Topic

POSTGRES_DSN = os.environ.get(
    "POSTGRES_DSN",
    "host=127.0.0.1 port=5433 dbname=seamlessgeolmap",
)
PG_ALIAS = "pg"


def _connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("INSTALL postgres; LOAD postgres;")
    con.execute("INSTALL spatial;  LOAD spatial;")
    con.execute(f"ATTACH '{POSTGRES_DSN}' AS {PG_ALIAS} (TYPE POSTGRES, READ_ONLY)")
    return con


def _describe(con: duckdb.DuckDBPyConnection, topic: Topic) -> list[tuple[str, str]]:
    """(column_name, column_type) for the topic's `_current` table."""
    rows = con.execute(
        f"DESCRIBE SELECT * FROM {PG_ALIAS}.{topic.schema}.{topic.layer}"
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def _geom_column(cols: list[tuple[str, str]]) -> str:
    for name, dtype in cols:
        if "GEOMETRY" in (dtype or "").upper():
            return name
    # Fallback: dataELT convention names it `geom`.
    for name, _ in cols:
        if name == "geom":
            return name
    raise RuntimeError("no GEOMETRY column found")


def read(topic: Topic) -> pa.Table:
    """Pull `{schema}.{topic}_current` into a pyarrow Table.

    `geom_wkb` is BLOB (server-side `ST_AsBinary`); every other column is
    passed through as the Postgres type maps it.
    """
    con = _connect()
    cols = _describe(con, topic)
    geom_col = _geom_column(cols)
    # Derive target_epsg from the geometry's own SRID — the WKB emitted by
    # ST_AsBinary is in that CRS, so ST_SRID is authoritative. Don't depend on a
    # literal target_epsg column (not all _current tables carry one yet). Drop
    # any existing target_epsg to avoid a duplicate. SRID 0 (unset) -> 4326.
    other = [c for c, _ in cols if c not in (geom_col, "target_epsg")]
    select_list = (
        ", ".join(f'"{c}"' for c in other)
        + f', ST_AsBinary("{geom_col}") AS geom_wkb'
        + f', COALESCE(NULLIF(ST_SRID("{geom_col}"), 0), 4326) AS target_epsg'
    )
    pg_sql = f'SELECT {select_list} FROM "{topic.schema}"."{topic.layer}"'
    return con.execute(
        "SELECT * FROM postgres_query(?, ?)",
        [PG_ALIAS, pg_sql],
    ).fetch_arrow_table()


def discover() -> list[Topic]:
    """Enumerate `_current` tables in MART_SCHEMAS via direct Postgres."""
    con = _connect()
    schema_list = ",".join(f"'{s}'" for s in MART_SCHEMAS)
    pg_sql = (
        "SELECT table_schema, table_name FROM information_schema.tables "
        r"WHERE table_name LIKE '%\_current' ESCAPE '\' "
        f"AND table_schema IN ({schema_list}) "
        "ORDER BY table_schema, table_name"
    )
    rows = con.execute(
        "SELECT * FROM postgres_query(?, ?)", [PG_ALIAS, pg_sql]
    ).fetchall()
    return [Topic(schema=s, layer=t) for s, t in rows]
