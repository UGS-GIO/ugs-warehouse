"""transform.materialize: hilbert order and feature_id, without a wide sort.

DuckDB spills a narrow sort but OOMs a wide one, so materialize() sorts only (rowid, hilbert, hash)
and reattaches the payload in chunks. These pin the two properties that buys — identical feature_ids
and identical physical order to a single global ORDER BY — plus the fact that it survives a memory
cap the global sort does not.
"""
from __future__ import annotations

import duckdb
import pytest

from ugs_warehouse.vector import transform


def _global_sort(rel: str) -> str:
    """The one-shot global sort materialize() replaces — the reference for the assertions below.

    Hashes the HYDRATED row, exactly as the original did; hashing the pre-hydration source instead
    breaks ties differently and the ids diverge.
    """
    return (
        f"WITH hydrated AS ({transform._select(rel)}) "
        f"SELECT *, row_number() OVER (ORDER BY ST_Hilbert(ST_Centroid(geom)), hash(hydrated)) "
        f"  AS feature_id "
        f"FROM hydrated ORDER BY ST_Hilbert(ST_Centroid(geom)), hash(hydrated)"
    )


def _con(max_memory: str = "512MB") -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    try:
        con.execute("INSTALL spatial; LOAD spatial;")
    except duckdb.Error as e:  # pragma: no cover - env without the extension
        pytest.skip(f"spatial extension unavailable: {e}")
    con.execute(f"SET max_memory='{max_memory}';")
    return con


def _seed(con, rows: int, pad: int = 60) -> str:
    """A source relation shaped like the real one: wide payload + a point geometry."""
    con.execute(f"""
        CREATE OR REPLACE TABLE src AS
        SELECT i, repeat('x', {pad}) AS pad,
               ST_Point(-112 + (i % 1000) / 1000.0, 39 + (i % 997) / 997.0) AS geom
        FROM range({rows}) s(i)
    """)
    # materialize() hydrates from WKB, so hand it the shape source.py does.
    return "(SELECT i, pad, ST_AsWKB(geom) AS geom_wkb, 4326 AS target_epsg FROM src)"


def _ids(con, table: str) -> list[tuple[int, int]]:
    return con.execute(f"SELECT i, feature_id FROM {table} ORDER BY i").fetchall()


def _is_physically_ordered(con, table: str) -> bool:
    gaps = con.execute(
        f"SELECT count(*) FROM (SELECT feature_id, lag(feature_id) OVER () AS p FROM {table}) "
        f"WHERE p IS NOT NULL AND feature_id <> p + 1"
    ).fetchone()[0]
    return gaps == 0


def test_feature_ids_match_a_single_global_sort():
    """The chunked path must be a drop-in: same ids, or every consumer's join key shifts."""
    con = _con()
    rel = _seed(con, 20_000)
    con.execute(f"CREATE OR REPLACE TABLE reference AS {_global_sort(rel)}")
    transform.materialize(con, rel, name="chunked")

    assert _ids(con, "chunked") == _ids(con, "reference")
    con.close()


def test_rows_are_physically_hilbert_ordered():
    """Parquet row-group bbox pruning depends on physical order, not just the id column."""
    con = _con()
    transform.materialize(con, _seed(con, 20_000), name="out")
    assert _is_physically_ordered(con, "out")
    con.close()


def test_feature_ids_are_deterministic_across_runs():
    """hash() tiebreak keeps ids stable run-to-run, which the skip-unchanged fingerprint relies on."""
    con = _con()
    rel = _seed(con, 5_000)
    transform.materialize(con, rel, name="first")
    transform.materialize(con, rel, name="second")
    assert _ids(con, "first") == _ids(con, "second")
    con.close()


def test_survives_a_cap_the_global_sort_dies_on():
    """The regression this exists for: an ingest OOMed here on 2026-09-01."""
    con = _con(max_memory="128MB")
    rel = _seed(con, 600_000, pad=400)

    with pytest.raises(duckdb.OutOfMemoryException):
        con.execute(f"CREATE OR REPLACE TABLE reference AS {_global_sort(rel)}")

    transform.materialize(con, rel, name="chunked")
    assert con.execute("SELECT count(*) FROM chunked").fetchone()[0] == 600_000
    assert _is_physically_ordered(con, "chunked")
    con.close()


def test_intermediate_tables_are_cleaned_up():
    con = _con()
    transform.materialize(con, _seed(con, 1_000), name="out")
    left = con.execute(
        "SELECT count(*) FROM duckdb_tables() WHERE table_name LIKE '_out_%'"
    ).fetchone()[0]
    assert left == 0
    con.close()


def test_partitioned_scans_give_the_same_result_as_one_scan():
    """Chunking the read must not change what lands: same ids, same order, same rows.

    It does NOT assert the memory benefit — a local table streams fine either way, so the real
    huc12 failure (a buffered postgres_query result) cannot be reproduced here.
    """
    con = _con()
    con.execute("""
        CREATE OR REPLACE TABLE src AS
        SELECT i, repeat('x', 60) AS pad,
               ST_Point(-112 + (i % 1000) / 1000.0, 39 + (i % 997) / 997.0) AS geom
        FROM range(20000) s(i)
    """)
    cols = "i, pad, ST_AsWKB(geom) AS geom_wkb, 4326 AS target_epsg"
    whole = f"(SELECT {cols} FROM src)"
    parts = [f"(SELECT {cols} FROM src WHERE i >= {lo} AND i < {lo + 5000})"
             for lo in range(0, 20000, 5000)]

    transform.materialize(con, whole, name="one_scan")
    transform.materialize(con, parts, name="partitioned")

    assert _ids(con, "partitioned") == _ids(con, "one_scan")
    assert _is_physically_ordered(con, "partitioned")
    con.close()
