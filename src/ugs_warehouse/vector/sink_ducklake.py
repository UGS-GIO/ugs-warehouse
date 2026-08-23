"""Write the transformed topic to DuckLake — native DuckDB geom + timestamps.

Each topic = one DuckLake table. On an ARMED topic (one carrying the durable `ugs_key`) we MERGE
delta-only, keyed on `ugs_key`, so an ingest writes just the changed/new/deleted rows instead of
rewriting 100% of the table every time (the old `CREATE OR REPLACE`). Dormant topics (no ugs_key)
keep `CREATE OR REPLACE` — the safety valve that keeps this shippable while most topics are
unarmed.

Change detection hashes every column EXCEPT `ugs_key` (the match key) and `feature_id` (a Hilbert
row-number that reshuffles whenever any other row is inserted/deleted — including it would flip
most rows' hash on any change and defeat the delta). Geometry IS hashed (proven: transform.py
already does `hash(hydrated)` over the geom column).

DuckLake rejects a single MERGE that carries both an UPDATE and a DELETE action ("MERGE INTO with
DuckLake only supports a single UPDATE/DELETE action"), so deletes run as a separate statement in
the same transaction — one atomic snapshot per ingest (verified on DuckDB 1.5.3 + DuckLake).

No WKB cast, no tz normalize — DuckLake stores native DuckDB types directly.
"""
from __future__ import annotations

import duckdb

from . import ducklake as catalog
from . import introspect
from .topics import Topic


def _q(col: str) -> str:
    """Double-quote a DuckDB identifier (columns come from DESCRIBE — already real names)."""
    return f'"{col}"'


def _target_columns(con: duckdb.DuckDBPyConnection, fqn: str) -> list[str] | None:
    """Column names of the existing DuckLake table, or None if it doesn't exist yet."""
    try:
        return [r[0] for r in con.execute(f"DESCRIBE {fqn}").fetchall()]
    except duckdb.Error:
        return None


def write(topic: Topic, con: duckdb.DuckDBPyConnection, view: str, append: bool = False) -> None:
    """Overwrite / MERGE / append the topic's DuckLake table from the transformed view."""
    alias = catalog.attach(con)
    con.execute(f"CREATE SCHEMA IF NOT EXISTS {alias}.{topic.schema}")
    fqn = f"{alias}.{topic.schema}.{topic.stem}"

    if append:
        con.execute(f"INSERT INTO {fqn} SELECT * FROM {view}")
        mode = "insert"
    else:
        cols = introspect.column_names(con, view)
        target = _target_columns(con, fqn)
        # MERGE only when the topic is armed AND the table already exists with a matching schema.
        # First armed ingest, dormant→armed (ugs_key column appears), or any column add/drop →
        # full rebuild; MERGE takes over on subsequent ingests.
        if introspect.UGS_KEY in cols and target is not None and target == cols:
            _merge(con, fqn, view, cols)
            mode = "merge"
        else:
            con.execute(f"CREATE OR REPLACE TABLE {fqn} AS SELECT * FROM {view}")
            mode = "create-or-replace"

    rows = con.execute(f"SELECT count(*) FROM {fqn}").fetchone()[0]
    print(f"[{topic.fqn}] ducklake: {rows} rows -> {fqn} ({mode})")


def _merge(con: duckdb.DuckDBPyConnection, fqn: str, view: str, cols: list[str]) -> None:
    """Delta-only upsert+delete keyed on ugs_key, in one transaction (one DuckLake snapshot).

    Two statements because DuckLake forbids UPDATE+DELETE in a single MERGE: (1) MERGE that
    updates changed rows + inserts new rows, (2) DELETE rows absent from the source.
    """
    diff = [c for c in cols if c not in (introspect.UGS_KEY, introspect.FEATURE_ID)]
    upd = [c for c in cols if c != introspect.UGS_KEY]
    key = _q(introspect.UGS_KEY)
    hash_t = ", ".join(f't.{_q(c)}' for c in diff)
    hash_s = ", ".join(f's.{_q(c)}' for c in diff)
    # A row with no content columns beyond feature_id can't "change" — omit the UPDATE branch.
    matched = (
        f"WHEN MATCHED AND (hash({hash_t}) IS DISTINCT FROM hash({hash_s})) THEN UPDATE SET "
        + ", ".join(f'{_q(c)} = s.{_q(c)}' for c in upd)
        if diff else ""
    )
    con.execute("BEGIN TRANSACTION")
    try:
        con.execute(
            f"MERGE INTO {fqn} AS t USING (SELECT * FROM {view}) AS s "
            f"ON t.{key} = s.{key} "
            f"{matched} "
            f"WHEN NOT MATCHED THEN INSERT ({', '.join(_q(c) for c in cols)}) "
            f"VALUES ({', '.join(f's.{_q(c)}' for c in cols)})"
        )
        con.execute(
            f"DELETE FROM {fqn} AS t "
            f"WHERE NOT EXISTS (SELECT 1 FROM {view} s WHERE s.{key} = t.{key})"
        )
        con.execute("COMMIT")
    except duckdb.Error:
        con.execute("ROLLBACK")
        raise
