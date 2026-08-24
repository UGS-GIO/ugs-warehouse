"""DuckLake MERGE sink: delta-only upsert+delete keyed on ugs_key, with the change-detector
excluding feature_id. Exercises the real `sink_ducklake._merge` against a scratch DuckLake.

The core correctness (verified on DuckDB 1.5.3 + DuckLake): a row whose ONLY change is feature_id
must NOT be rewritten — feature_id is a Hilbert row-number that reshuffles on any insert/delete,
so including it in the hashdiff would churn ~100% of rows on any change and defeat the merge.
"""
from __future__ import annotations

import tempfile

import duckdb
import pytest

from ugs_warehouse.vector import introspect, sink_ducklake

COLS = ["ugs_key", "name", "faultage", "geom", "feature_id"]


@pytest.fixture()
def ducklake_con():
    con = duckdb.connect()
    try:
        con.execute("INSTALL ducklake; LOAD ducklake; INSTALL spatial; LOAD spatial;")
    except duckdb.Error as e:
        pytest.skip(f"ducklake extension unavailable: {e}")
    d = tempfile.mkdtemp(prefix="ducklake-test-")
    con.execute(f"ATTACH 'ducklake:{d}/catalog.ducklake' AS w (DATA_PATH '{d}/data')")
    con.execute("CREATE SCHEMA IF NOT EXISTS w.hazards")
    yield con
    con.close()


def _seed_target(con):
    con.execute(
        "CREATE TABLE w.hazards.t AS SELECT * FROM (VALUES "
        "(1,'a','old',ST_Point(-111,39),10), "   # unchanged content
        "(2,'b','old',ST_Point(-112,40),20), "   # will be edited
        "(4,'d','old',ST_Point(-113,41),40)"     # will be deleted (absent from source)
        ") v(ugs_key,name,faultage,geom,feature_id)"
    )


def _source(con):
    con.execute(
        "CREATE TABLE src AS SELECT * FROM (VALUES "
        "(1,'a','old',ST_Point(-111,39),99), "   # ONLY feature_id changed (10->99) -> must NOT update
        "(2,'b2','old',ST_Point(-112,40),21), "  # name edited -> update
        "(3,'c','new',ST_Point(-114,42),30)"     # new -> insert
        ") v(ugs_key,name,faultage,geom,feature_id)"
    )


def test_merge_delta_and_feature_id_excluded(ducklake_con):
    con = ducklake_con
    _seed_target(con)
    _source(con)

    sink_ducklake._merge(con, "w.hazards.t", "src", COLS)

    rows = {r[0]: r for r in con.execute(
        "SELECT ugs_key, name, feature_id FROM w.hazards.t ORDER BY ugs_key").fetchall()}

    assert set(rows) == {1, 2, 3}                       # row 4 deleted, row 3 inserted
    assert rows[1][2] == 10                             # Finding C: feature_id-only change did NOT update
    assert rows[1][1] == "a"                            # row 1 content untouched
    assert rows[2] == (2, "b2", 21)                     # row 2 updated
    assert rows[3][1] == "c"                            # row 3 inserted


def test_merge_raises_on_null_key(ducklake_con):
    """A NULL ugs_key must fail loud, not silently vanish. A NULL never matches the MERGE `ON`, so it
    falls into WHEN NOT MATCHED (inserted) and is then removed by the DELETE in the same transaction
    — the row would disappear from DuckLake while the GeoParquet/PMTiles from the same run still
    carry it. The guard refuses before touching the table."""
    con = ducklake_con
    _seed_target(con)
    con.execute(
        "CREATE TABLE src AS SELECT * FROM (VALUES "
        "(1,'a','old',ST_Point(-111,39),10), "
        "(NULL,'z','new',ST_Point(-115,43),50)"   # NULL key — must trigger the guard
        ") v(ugs_key,name,faultage,geom,feature_id)"
    )
    with pytest.raises(ValueError, match="NULL ugs_key"):
        sink_ducklake._merge(con, "w.hazards.t", "src", COLS)
    # guard fires before BEGIN → the target is untouched (no partial insert/delete)
    survivors = {r[0] for r in con.execute("SELECT ugs_key FROM w.hazards.t").fetchall()}
    assert survivors == {1, 2, 4}


def test_ducklake_preserves_describe_types_so_guard_merges(ducklake_con):
    """The rebuild guard in `write` MERGEs only when the source view and the existing DuckLake table
    have identical (name, type) schemas. That relies on DuckLake preserving each column's DESCRIBE
    type string across `CREATE OR REPLACE ... AS SELECT *`. If a type normalized on storage (esp.
    TIMESTAMP WITH TIME ZONE, TIMESTAMP_NS, GEOMETRY), the guard would false-mismatch on every later
    ingest and silently fall back to CREATE OR REPLACE — defeating the delta-merge with no error.
    Assert the round-trip is faithful for the types armed topics carry."""
    con = ducklake_con
    con.execute(
        "CREATE VIEW src_typed AS SELECT "
        "CAST(1 AS BIGINT) AS ugs_key, CAST(1 AS BIGINT) AS feature_id, "
        "CAST('x' AS VARCHAR) AS name, CAST(1.5 AS DOUBLE) AS val, "
        "CAST(1 AS INTEGER) AS ogc_fid, CAST('2020-01-02' AS DATE) AS d, "
        "CAST('2020-01-02 03:04:05' AS TIMESTAMP WITH TIME ZONE) AS ts_tz, "
        "CAST('2020-01-02 03:04:05' AS TIMESTAMP_NS) AS ts_ns, "
        "ST_Point(-111,39) AS geom"
    )
    con.execute("CREATE OR REPLACE TABLE w.hazards.typed AS SELECT * FROM src_typed")

    assert introspect.column_schema(con, "src_typed") \
        == introspect.column_schema(con, "w.hazards.typed")


def test_merge_is_idempotent(ducklake_con):
    """Re-merging the same source is a no-op — the hashdiff means unchanged rows don't churn.
    (ugs_key uniqueness itself is guaranteed upstream by the serving-layer pre-swap UNIQUE, so
    the warehouse never sees a duplicate; the MERGE doesn't re-police that.)"""
    con = ducklake_con
    _seed_target(con)
    _source(con)
    sink_ducklake._merge(con, "w.hazards.t", "src", COLS)
    before = con.execute("SELECT * FROM w.hazards.t ORDER BY ugs_key").fetchall()

    sink_ducklake._merge(con, "w.hazards.t", "src", COLS)   # second identical merge
    after = con.execute("SELECT * FROM w.hazards.t ORDER BY ugs_key").fetchall()

    assert before == after   # nothing changed on the re-run
