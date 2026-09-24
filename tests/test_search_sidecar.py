from __future__ import annotations

import duckdb

from ugs_warehouse.vector import sink_archive


def test_search_sidecar_is_file_order_text_as_duckdb_casts_it(tmp_path):
    con = duckdb.connect()
    con.execute(
        "CREATE TABLE t AS SELECT * FROM (VALUES "
        "(2, 'Fan B', 1.0, DATE '2026-01-02', 'x'::BLOB), "
        "(1, 'Fan A', NULL, DATE '2026-01-01', 'x'::BLOB)) v(feature_id, name, area, surveyed, geom)"
    )
    path = str(tmp_path / "s.parquet")
    assert sink_archive._copy_search(con, "t", path)
    rows = con.execute(f"SELECT text FROM '{path}'").fetchall()
    assert rows == [("fan a\n2026-01-01",), ("fan b\n1.0\n2026-01-02",)]


def test_no_sidecar_without_searchable_columns(tmp_path):
    con = duckdb.connect()
    con.execute("CREATE TABLE t AS SELECT 1 AS feature_id, 'x'::BLOB AS geom")
    assert not sink_archive._copy_search(con, "t", str(tmp_path / "s.parquet"))
