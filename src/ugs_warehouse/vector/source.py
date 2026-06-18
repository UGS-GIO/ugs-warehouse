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
    con.execute("SET max_memory='128MB';")
    con.execute("INSTALL postgres; LOAD postgres;")
    con.execute("INSTALL spatial;  LOAD spatial;")
    # Set password if provided separately
    password = os.environ.get("PGPASSWORD")
    if password:
        dsn = f"{POSTGRES_DSN} password={password}"
    else:
        dsn = POSTGRES_DSN
    con.execute(f"ATTACH '{dsn}' AS {PG_ALIAS} (TYPE POSTGRES, READ_ONLY)")
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
    try:
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
    finally:
        con.close()


def get_count(topic: Topic) -> int:
    """Get the total row count of the topic's `_current` table directly from Postgres."""
    con = _connect()
    try:
        pg_sql = f'SELECT count(*) FROM "{topic.schema}"."{topic.layer}"'
        row = con.execute(
            "SELECT * FROM postgres_query(?, ?)",
            [PG_ALIAS, pg_sql],
        ).fetchone()
        return int(row[0]) if row else 0
    finally:
        con.close()


def read_chunk(topic: Topic, limit: int, offset: int) -> pa.Table:
    """Pull a chunk of `{schema}.{topic}_current` into a pyarrow Table."""
    con = _connect()
    try:
        cols = _describe(con, topic)
        geom_col = _geom_column(cols)
        other = [c for c, _ in cols if c not in (geom_col, "target_epsg")]
        select_list = (
            ", ".join(f'"{c}"' for c in other)
            + f', ST_AsBinary("{geom_col}") AS geom_wkb'
            + f', COALESCE(NULLIF(ST_SRID("{geom_col}"), 0), 4326) AS target_epsg'
        )
        pg_sql = f'SELECT {select_list} FROM "{topic.schema}"."{topic.layer}" LIMIT {limit} OFFSET {offset}'
        return con.execute(
            "SELECT * FROM postgres_query(?, ?)",
            [PG_ALIAS, pg_sql],
        ).fetch_arrow_table()
    finally:
        con.close()


# Descriptive (catalog) metadata columns on raw.schema_registry (ugs-ingest #171).
_META_COLS = ("display_name", "description", "keywords", "iso_topic_category",
              "use_constraints", "lineage", "point_of_contact")


def read_metadata(topic: Topic) -> dict:
    """Per-topic descriptive metadata from `raw.schema_registry` (keyed by domain_topic,
    which equals the topic stem). Graceful: returns {} if the columns/table/grant aren't
    there yet (pre-#171, or no SELECT on raw) — the warehouse then falls back to defaults.
    """
    stem = topic.stem.replace("'", "''")
    pg_sql = (
        "SELECT " + ", ".join(_META_COLS)
        + f" FROM raw.schema_registry WHERE domain_topic = '{stem}'"
        + " ORDER BY (status = 'active') DESC LIMIT 1"
    )
    con = _connect()
    try:
        row = con.execute(
            "SELECT * FROM postgres_query(?, ?)", [PG_ALIAS, pg_sql]
        ).fetchone()
    except Exception:  # noqa: BLE001 — missing columns/table/grant → fall back to defaults
        return {}
    finally:
        con.close()
    if not row:
        return {}
    return {k: v for k, v in zip(_META_COLS, row, strict=False) if v not in (None, "", [])}


def discover() -> list[Topic]:
    """Enumerate `_current` tables in MART_SCHEMAS via direct Postgres."""
    con = _connect()
    try:
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
    finally:
        con.close()
