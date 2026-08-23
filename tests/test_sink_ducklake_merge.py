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

from ugs_warehouse.vector import sink_ducklake

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


def test_duplicate_ugs_key_in_source_fails_loud(ducklake_con):
    """A duplicate ugs_key means the durable key isn't unique — MERGE must raise, not silently
    corrupt (backstop to the serving-layer pre-swap UNIQUE)."""
    con = ducklake_con
    _seed_target(con)
    con.execute(
        "CREATE TABLE src AS SELECT * FROM (VALUES "
        "(1,'a','old',ST_Point(-111,39),10), "
        "(1,'dup','old',ST_Point(-111,39),11)"
        ") v(ugs_key,name,faultage,geom,feature_id)"
    )
    with pytest.raises(duckdb.Error):
        sink_ducklake._merge(con, "w.hazards.t", "src", COLS)
