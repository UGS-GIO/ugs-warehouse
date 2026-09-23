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

Consequence: on a MERGEd table `feature_id` is NOT authoritative. The delta only refreshes it on
content-changed rows, so unchanged rows keep their old Hilbert number and it can repeat within the
table. This intentionally breaks, for the DuckLake asset only, the transform.py invariant that the
GeoParquet and PMTiles share one feature_id — on armed topics the durable identity is `ugs_key`, and
consumers must use it. The GeoParquet (rebuilt whole each ingest) keeps a fresh, unique feature_id.

DuckLake rejects a single MERGE that carries both an UPDATE and a DELETE action ("MERGE INTO with
DuckLake only supports a single UPDATE/DELETE action"), so deletes run as a separate statement in
the same transaction — one atomic snapshot per ingest (verified on DuckDB 1.5.3 + DuckLake).

No WKB cast, no tz normalize — DuckLake stores native DuckDB types directly.
"""
from __future__ import annotations

import sys

import duckdb

from . import ducklake as catalog
from . import introspect
from .topics import Topic


def _q(col: str) -> str:
    """Double-quote a DuckDB identifier (columns come from DESCRIBE — already real names)."""
    return f'"{col}"'


def _target_schema(con: duckdb.DuckDBPyConnection, fqn: str) -> list[tuple[str, str]] | None:
    """(name, type) pairs of the existing DuckLake table, or None if it doesn't exist yet."""
    try:
        return [(r[0], r[1]) for r in con.execute(f"DESCRIBE {fqn}").fetchall()]
    except duckdb.Error:
        return None


def _is_dearm(target_schema: list[tuple[str, str]] | None, cols: list[str]) -> bool:
    """A previously-armed target (its DuckLake table carries ugs_key) receiving a source with NO
    ugs_key — a producer-side de-arm (governance C4). Distinct from a dormant topic that never had
    the key (the normal CREATE-OR-REPLACE path) and from a brand-new table."""
    if target_schema is None:
        return False
    target_has_key = any(name == introspect.UGS_KEY for name, _ in target_schema)
    return target_has_key and introspect.UGS_KEY not in cols


def write(topic: Topic, con: duckdb.DuckDBPyConnection, view: str, append: bool = False) -> None:
    """Overwrite / MERGE / append the topic's DuckLake table from the transformed view."""
    alias = catalog.attach(con)
    con.execute(f"CREATE SCHEMA IF NOT EXISTS {alias}.{topic.schema}")
    fqn = f"{alias}.{topic.schema}.{topic.stem}"

    if append:
        # NOTE: append is a plain INSERT and does NOT run the C4 de-arm check below. It has no live
        # caller today (ingest.py calls write() with append defaulting False); if chunked append is
        # reintroduced for armed topics, a de-arm here would skip the warning — though a narrower
        # de-armed source INSERTed into a still-armed target raises duckdb.Error, caught loudly upstream.
        con.execute(f"INSERT INTO {fqn} SELECT * FROM {view}")
        mode = "insert"
    else:
        src_schema = introspect.column_schema(con, view)
        cols = [name for name, _ in src_schema]
        target_schema = _target_schema(con, fqn)
        # MERGE only when the topic is armed AND the table already exists with an identical schema —
        # (name, type) pairs, not just names, so a rename-free type change (e.g. VARCHAR→BIGINT)
        # routes to the CREATE OR REPLACE rebuild instead of MERGEing against a mismatched target.
        # First armed ingest, dormant→armed (ugs_key column appears), or any column/type change →
        # full rebuild; MERGE takes over on subsequent ingests.
        if introspect.UGS_KEY in cols and target_schema is not None and target_schema == src_schema:
            _merge(con, fqn, view, cols)
            mode = "merge"
        else:
            # C4: a previously-armed topic arriving with no ugs_key is a producer-side de-arm.
            # A silent full rewrite would drop durable identity (row comments, deep links,
            # non-churning merge) for existing rows. Warn loudly — but still proceed, because a
            # deliberate de-arm is legitimate; visibility, not a refusal.
            if _is_dearm(target_schema, cols):
                print(
                    f"[{topic.fqn}] WARNING: previously-armed topic arrived with no "
                    f"{introspect.UGS_KEY} — reverting to full-rewrite; durable identity will break "
                    f"for existing rows. Producer-side de-arm — investigate before it recurs.",
                    file=sys.stderr,  # stderr → ERROR severity in Cloud Run, matching the module's other alerts
                )
            con.execute(f"CREATE OR REPLACE TABLE {fqn} AS SELECT * FROM {view}")
            mode = "create-or-replace"

    rows = con.execute(f"SELECT count(*) FROM {fqn}").fetchone()[0]
    print(f"[{topic.fqn}] ducklake: {rows} rows -> {fqn} ({mode})")


def _merge(con: duckdb.DuckDBPyConnection, fqn: str, view: str, cols: list[str]) -> None:
    """Delta-only upsert+delete keyed on ugs_key, in one transaction (one DuckLake snapshot).

    Change detection runs as a NARROW pre-diff on `(ugs_key, content-hash)` only — geometry (or any
    wide column) never enters a join. The previous form put the change test inside the MERGE,
    `WHEN MATCHED AND hash(t.<all cols>) IS DISTINCT FROM hash(s.<all cols>)`, which forced DuckDB's
    join to carry every column of both sides: a ~3 GB hash table, ~2.5 GB of it geometry. It fit
    under the memory cap, so DuckDB kept it fully resident and then OOM'd on the transient buffers
    needed to decompress the target's geometry column alongside it — the outline layer's ingest
    (426k polygons) died there at a 6 GB cap in a 16 GiB container. Comparing a scalar `hash(...)`
    keeps the diff a few bytes wide, so the write step below only touches the rows that actually
    changed and the whole merge runs in bounded memory (~1-2 GB regardless of layer size).

    Then two write statements, because DuckLake forbids UPDATE+DELETE in one MERGE: (1) MERGE the
    changed/new rows, (2) DELETE rows absent from the source. The diff + both writes are one
    transaction — one atomic DuckLake snapshot per ingest.

    Fails loud on a NULL key: a Postgres UNIQUE permits NULLs, and a NULL `ugs_key` never matches the
    `ON` join, so it would fall into WHEN NOT MATCHED (inserted) and then be removed by the DELETE in
    the same transaction — the row would vanish from DuckLake while the GeoParquet/PMTiles from the
    same run still carry it. The serving pre-swap verify guarantees non-null upstream; if one reaches
    here anyway we refuse rather than silently drop it.
    """
    diff = [c for c in cols if c not in (introspect.UGS_KEY, introspect.FEATURE_ID)]
    upd = [c for c in cols if c != introspect.UGS_KEY]
    key = _q(introspect.UGS_KEY)
    nulls = con.execute(f"SELECT count(*) FROM {view} WHERE {key} IS NULL").fetchone()[0]
    if nulls:
        raise ValueError(
            f"{fqn}: {nulls} source row(s) with NULL {introspect.UGS_KEY} — refusing to MERGE "
            f"(a NULL key never matches ON, so it would be inserted then deleted in the same "
            f"transaction and silently vanish from DuckLake). Fix the serving-layer key upstream."
        )
    # Changed/new keys, projecting ONLY (ugs_key, hash) so geometry never enters the join. `hash()`
    # over the `diff` columns is the same 64-bit content hash the old MERGE predicate used (identical
    # change semantics) — just computed below the join instead of after it. A row with no content
    # columns beyond feature_id can't "change", so only brand-new keys need a write.
    hsel = (", hash(" + ", ".join(_q(c) for c in diff) + ") AS _h") if diff else ""
    changed = f"t.{key} IS NULL OR t._h IS DISTINCT FROM s._h" if diff else f"t.{key} IS NULL"
    # Restrict the target scan to keys the source also carries: a LEFT JOIN from source→target only
    # matches those rows anyway, so this skips hashing target rows about to be DELETEd (absent from
    # the source) and keeps the diff proportional to the source if `view` is ever a partial delta
    # rather than the full transformed layer. New keys still fall through via `t.<key> IS NULL`.
    con.execute(
        f"CREATE OR REPLACE TEMP TABLE _ducklake_delta AS "
        f"SELECT s.{key} AS {key} "
        f"FROM (SELECT {key}{hsel} FROM {view}) s "
        f"LEFT JOIN (SELECT {key}{hsel} FROM {fqn} WHERE {key} IN (SELECT {key} FROM {view})) t "
        f"ON s.{key} = t.{key} "
        f"WHERE {changed}"
    )
    # Restricted to changed/new keys, so WHEN MATCHED is always a known content change (no per-row
    # hash predicate) and the source scan is the delta, not the whole layer.
    matched = ("WHEN MATCHED THEN UPDATE SET "
               + ", ".join(f'{_q(c)} = s.{_q(c)}' for c in upd) + " ") if diff else ""
    con.execute("BEGIN TRANSACTION")
    try:
        con.execute(
            f"MERGE INTO {fqn} AS t "
            f"USING (SELECT * FROM {view} WHERE {key} IN (SELECT {key} FROM _ducklake_delta)) AS s "
            f"ON t.{key} = s.{key} "
            f"{matched}"
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
    finally:
        # Cleanup must not mask an in-flight error (e.g. the OOM this rewrite targets): a DROP that
        # itself throws only does so on an already-dead connection, where the real exception matters.
        try:
            con.execute("DROP TABLE IF EXISTS _ducklake_delta")
        except duckdb.Error:
            pass
