"""Read `{schema}.{topic}_current` from Postgres and stream it through DuckDB.

`stream_transformed` ATTACHes Postgres and hands a transformed table to the sinks — all in one
DuckDB connection, no pyarrow, no Python-held rows (DuckDB streams + spills under its memory
cap). PostGIS geometry crosses as `geom_wkb` (server-side `ST_AsBinary`); the geometry's own
SRID becomes `target_epsg` so `transform` confirms or reprojects to 4326.

Env:
  POSTGRES_DSN  libpq-style DSN.
                Local dev: `cloud_sql_proxy` on localhost.
                Prod (Cloud Run): Cloud SQL Connector instance DSN.
"""
from __future__ import annotations

import json
import os
import sys

import duckdb

from .topics import MART_SCHEMAS, TABLE_SUFFIX, Topic

POSTGRES_DSN = os.environ.get(
    "POSTGRES_DSN",
    "host=127.0.0.1 port=5433 dbname=seamlessgeolmap",
)
PG_ALIAS = "pg"


def _connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    max_mem = os.environ.get("DUCKDB_MAX_MEMORY", "128MB")
    con.execute(f"SET max_memory='{max_mem}';")
    con.execute("INSTALL postgres; LOAD postgres;")
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


def stream_transformed(topic: Topic) -> tuple[duckdb.DuckDBPyConnection, str]:
    """Prod streaming path: Postgres → transform → materialize, ALL in one DuckDB connection —
    no pyarrow, no rows ever in Python. DuckDB streams the postgres scan and spills the global
    hilbert sort under its memory cap. Returns (connection, table_name) for the sinks to read.

    Faster than the arrow+chunk path (no duckdb→python→duckdb copy, extensions loaded once,
    one global sort instead of per-chunk sorts) and lower-memory (bounded by DuckDB spill, not a
    Python-held arrow table).
    """
    from . import transform
    con = _connect()           # postgres ATTACHed (read-only) + spatial
    transform.setup(con)       # memory cap + spatial + h3
    cols = _describe(con, topic)
    geom_col = _geom_column(cols)
    other = [c for c, _ in cols if c not in (geom_col, "target_epsg")]
    select_list = (
        ", ".join(f'"{c}"' for c in other)
        + f', ST_AsBinary("{geom_col}") AS geom_wkb'
        + f', ST_SRID("{geom_col}") AS target_epsg'  # 0 = unstamped; transform errors, never assumes
    )
    pg_sql = f'SELECT {select_list} FROM "{topic.schema}"."{topic.layer}"'
    # postgres_query subquery as the transform source; $pgq$ dollar-quote avoids escaping.
    source_rel = f"(SELECT * FROM postgres_query('{PG_ALIAS}', $pgq${pg_sql}$pgq$))"
    return con, transform.materialize(con, source_rel)


# Descriptive (catalog) metadata columns on raw.schema_registry (ugs-ingest #171).
_META_COLS = ("display_name", "description", "keywords", "iso_topic_category",
              "use_constraints", "lineage", "point_of_contact")

# Columns that are jsonb in Postgres. They are SELECTed with an explicit ::text cast and parsed
# on the way out, so callers always receive a real Python value — same approach related.py takes
# with `relationships::text`. Without the cast the driver hands back the raw JSON *string*, and
# anything treating it as a sequence (e.g. `list(...)`) silently splits it into characters.
_JSON_COLS = frozenset({"keywords"})


def _select_expr(col: str) -> str:
    """Column reference for the metadata SELECT — jsonb columns cast to text for parsing."""
    return f"{col}::text" if col in _JSON_COLS else col


def read_metadata(topic: Topic) -> dict:
    """Per-topic descriptive metadata from `raw.schema_registry` (keyed by domain_topic,
    which equals the topic stem). Graceful: returns {} if the columns/table/grant aren't
    there yet (pre-#171, or no SELECT on raw) — the warehouse then falls back to defaults.

    `domain_topic` is the primary key, so this matches at most one row and needs no ordering.

    jsonb columns come back parsed (see `_JSON_COLS`); a malformed value raises rather than
    degrading, since that is a registry problem worth seeing rather than silently dropping.
    """
    stem = topic.stem.replace("'", "''")
    pg_sql = (
        "SELECT " + ", ".join(_select_expr(c) for c in _META_COLS)
        + f" FROM raw.schema_registry WHERE domain_topic = '{stem}'"
    )
    con = _connect()
    try:
        row = con.execute(
            "SELECT * FROM postgres_query(?, ?)", [PG_ALIAS, pg_sql]
        ).fetchone()
    except Exception as e:  # noqa: BLE001 — missing columns/table/grant → fall back to defaults
        # Say so. An uncurated topic and a broken query both produce {} here, and staying
        # silent about the second is what let a bad ORDER BY suppress this read for six weeks
        # while looking exactly like "nobody has curated anything yet".
        print(f"[{topic.fqn}] catalog metadata read FAILED, falling back to defaults: {e}",
              file=sys.stderr)
        return {}
    finally:
        con.close()
    if not row:
        return {}
    meta = dict(zip(_META_COLS, row, strict=False))
    # Parse before the empty-filter below, so an empty jsonb array is dropped as `[]` rather
    # than kept as the truthy string "[]". Not guarded: a value that will not parse is a
    # malformed registry row, and swallowing it here is how the previous bug stayed invisible.
    for col in _JSON_COLS:
        if meta.get(col) is not None:
            meta[col] = json.loads(meta[col])
    return {k: v for k, v in meta.items() if v not in (None, "", [])}


def discover() -> list[Topic]:
    """Enumerate serving tables in MART_SCHEMAS via direct Postgres. Suffix is TABLE_SUFFIX
    (`_current` by default, `_review` for the gated pre-release build)."""
    con = _connect()
    try:
        schema_list = ",".join(f"'{s}'" for s in MART_SCHEMAS)
        # Escape the suffix's underscores so LIKE treats them literally (TABLE_SUFFIX is validated to
        # `_[a-z_]+` at import, so inlining it here is safe).
        like = "%" + TABLE_SUFFIX.replace("_", r"\_")
        pg_sql = (
            "SELECT table_schema, table_name FROM information_schema.tables "
            f"WHERE table_name LIKE '{like}' ESCAPE '\\' "
            f"AND table_schema IN ({schema_list}) "
            "ORDER BY table_schema, table_name"
        )
        rows = con.execute(
            "SELECT * FROM postgres_query(?, ?)", [PG_ALIAS, pg_sql]
        ).fetchall()
        return [Topic(schema=s, layer=t) for s, t in rows]
    finally:
        con.close()
