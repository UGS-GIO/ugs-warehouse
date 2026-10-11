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

from ..core import identifiers
from .topics import MART_SCHEMAS, TABLE_SUFFIX, Topic

POSTGRES_DSN = os.environ.get(
    "POSTGRES_DSN",
    "host=127.0.0.1 port=5433 dbname=seamlessgeolmap",
)
PG_ALIAS = "pg"

# Postgres heap pages per hydrate chunk. ctid ranges, not LIMIT/OFFSET — OFFSET rescans from the
# top each time and turns a chunked read into O(n^2). 0 disables chunking.
PAGES_PER_CHUNK = int(os.environ.get("INGEST_PAGES_PER_CHUNK", "2000"))
# Below this total size (incl. TOAST) a table is read in one scan.
CHUNK_TARGET_BYTES = int(os.environ.get("INGEST_CHUNK_TARGET_BYTES", str(16 * 1024 * 1024)))


def _connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    max_mem = os.environ.get("DUCKDB_MAX_MEMORY", "128MB")
    con.execute(f"SET max_memory='{max_mem}';")
    con.execute("INSTALL postgres; LOAD postgres;")
    # libpq reads PGPASSWORD from the environment.
    dsn = POSTGRES_DSN.replace("'", "''")
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


def _scan_chunks(con: duckdb.DuckDBPyConnection, rel: str, select_list: str) -> list[str]:
    """Partition the table into ctid page ranges — disjoint, index-free, and no ordering needed
    (feature_id is assigned later from its own sort). Falls back to one scan if sizing fails."""
    def scan(where: str = "") -> str:
        pg_sql = f"SELECT {select_list} FROM {rel} {where}"
        return f"(SELECT * FROM postgres_query('{PG_ALIAS}', $pgq${pg_sql}$pgq$))"

    if PAGES_PER_CHUNK <= 0:
        return [scan()]
    # heap pages address the ctid ranges, but PostGIS keeps big geometry in TOAST — out of line and
    # NOT counted by pg_relation_size. Sizing on the heap alone reads a polygon table as tiny and
    # hands back one unchunked scan. total/heap is how much each heap page really drags in.
    size_sql = (f"SELECT pg_relation_size('{rel}'::regclass) / "
                f"current_setting('block_size')::int AS pages, "
                f"pg_relation_size('{rel}'::regclass) AS heap, "
                f"pg_total_relation_size('{rel}'::regclass) AS total")
    try:
        pages, heap, total = con.execute(
            f"SELECT pages, heap, total FROM postgres_query('{PG_ALIAS}', $pgq${size_sql}$pgq$)"
        ).fetchone()
    except duckdb.Error:
        return [scan()]
    if not pages or not total or total <= CHUNK_TARGET_BYTES:
        return [scan()]

    expansion = max(total / max(heap, 1), 1.0)
    per_chunk = max(int(PAGES_PER_CHUNK / expansion), 1)
    chunks = [
        scan(f"WHERE ctid >= '({lo},0)'::tid AND ctid < '({lo + per_chunk},0)'::tid")
        for lo in range(0, pages, per_chunk)
    ]
    # Open-ended tail: pages is a snapshot, and anything written past it must not be dropped.
    last = (pages // per_chunk + 1) * per_chunk
    chunks.append(scan(f"WHERE ctid >= '({last},0)'::tid"))
    return chunks


def stream_transformed(topic: Topic) -> tuple[duckdb.DuckDBPyConnection, str]:
    """Prod streaming path: Postgres → transform → materialize, ALL in one DuckDB connection —
    no pyarrow, no rows ever in Python. DuckDB streams the postgres scan and spills the global
    hilbert sort under its memory cap. Returns (connection, table_name) for the sinks to read.

    Faster than the arrow+chunk path (no duckdb→python→duckdb copy, extensions loaded once,
    one global sort instead of per-chunk sorts) and lower-memory (bounded by DuckDB spill, not a
    Python-held arrow table).

    The connection outlives this call (the sinks read the materialized table through it), so the
    CALLER closes it — `ingest._ingest`, in a finally. A failure before the handoff closes it here,
    since no caller holds it yet.
    """
    from . import transform
    con = _connect()           # postgres ATTACHed (read-only) + spatial
    try:
        transform.setup(con)       # memory cap + spatial + h3
        cols = _describe(con, topic)
        geom_col = _geom_column(cols)
        other = [c for c, _ in cols if c not in (geom_col, "target_epsg")]
        # Interpolated below like the table name is, and `$` is legal in an unquoted PG identifier
        # after the first char, so `x$pgq$...` ends the fence. Raise, don't drop: a missing column
        # would publish an artifact short of its data.
        for c in (*other, geom_col):
            identifiers.require_identifier(f"column in {topic.fqn}", c)
        select_list = (
            ", ".join(f'"{c}"' for c in other)
            + f', ST_AsBinary("{geom_col}") AS geom_wkb'
            + f', ST_SRID("{geom_col}") AS target_epsg'  # 0 = unstamped; transform errors, never assumes
        )
        # postgres_query subquery as the transform source; $pgq$ dollar-quote avoids escaping.
        rel = f'"{topic.schema}"."{topic.layer}"'
        return con, transform.materialize(con, _scan_chunks(con, rel, select_list))
    except BaseException:
        con.close()
        raise


# Descriptive (catalog) metadata columns on raw.schema_registry (ugs-ingest #171).
_META_COLS = ("display_name", "description", "keywords", "iso_topic_category",
              "use_constraints", "lineage", "point_of_contact")


def _row_as_json_sql(where: str) -> str:
    """The metadata SELECT, wrapped so Postgres serializes the whole row to JSON.

    A jsonb column read straight through the scanner arrives as the raw JSON *string*, and
    anything treating that as a sequence (e.g. `list(...)`) splits it into characters — #64, which
    published per-character keywords on all 27 items. Casting the known-jsonb columns one by one
    fixes the instance but leaves a list of column types maintained here, in a different system
    from the DDL that defines them; the columns nobody has curated yet are the ones it silently
    gets wrong. `to_jsonb` moves that knowledge back to Postgres: every column comes back with its
    own type (jsonb → list/dict, text → str, and a *text* column whose contents merely look like
    JSON stays a str), so adding a jsonb column to the registry needs no change here.
    """
    inner = "SELECT " + ", ".join(_META_COLS) + f" FROM raw.schema_registry WHERE {where}"
    return f"SELECT to_jsonb(m)::text FROM ({inner}) m"


# Columns the catalog treats as a sequence rather than a value. `to_jsonb` gets the TYPE right
# (jsonb → list, text → str) but says nothing about SHAPE: a jsonb scalar is a valid document, so
# `keywords = '"counties"'` parses cleanly to a str and `list(...)` then publishes eight
# one-character keywords — #64's output with the parse looking correct. Nothing writes a scalar
# today (the curation tool always emits an array); this is so a row that does is loud.
_LIST_COLS = ("keywords",)


def _checked_shapes(meta: dict) -> dict:
    for col in _LIST_COLS:
        if col in meta and not isinstance(meta[col], list):
            raise TypeError(
                f"raw.schema_registry.{col} must be a JSON array, got "
                f"{type(meta[col]).__name__} {meta[col]!r} — fix the registry row",
            )
    return meta


def read_metadata(topic: Topic) -> dict:
    """Per-topic descriptive metadata from `raw.schema_registry` (keyed by domain_topic,
    which equals the topic stem). Graceful: returns {} if the columns/table/grant aren't
    there yet (pre-#171, or no SELECT on raw) — the warehouse then falls back to defaults.

    `domain_topic` is the primary key, so this matches at most one row and needs no ordering.

    Values come back typed by Postgres (see `_row_as_json_sql`); a malformed value raises rather
    than degrading, since that is a registry problem worth seeing rather than silently dropping.
    """
    stem = topic.stem.replace("'", "''")
    pg_sql = _row_as_json_sql(f"domain_topic = '{stem}'")
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
    if not row or row[0] is None:
        return {}
    # Parse before the empty-filter, so an empty jsonb array reaches it as `[]` rather than as the
    # truthy string "[]". Not guarded: a row that will not parse is a malformed registry row, and
    # swallowing it here is how the previous bug stayed invisible. Keyed by column name, so this
    # no longer depends on the SELECT order lining up with _META_COLS.
    return _checked_shapes({k: v for k, v in json.loads(row[0]).items()
                            if v not in (None, "", [], {})})


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
        # Skip per row rather than abort the sweep: `Topic` rejects names it can't safely
        # interpolate, and one such table in a mart schema must not cost every other topic its
        # reingest.
        out = []
        for s, t in rows:
            try:
                out.append(Topic(schema=s, layer=t))
            except ValueError as e:
                print(f"discover: skipping {s}.{t} — {e}", file=sys.stderr)
        return out
    finally:
        con.close()
