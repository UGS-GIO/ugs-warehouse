"""The archive writer keeps every column type a `_current` table can carry, as DuckDB's COPY did."""
from __future__ import annotations

from decimal import Decimal

import duckdb

from ugs_warehouse.vector import sink_archive


def test_every_column_type_survives_the_archive(tmp_path) -> None:
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial")
    con.execute(
        "CREATE TABLE v AS SELECT 1 AS feature_id, INTERVAL 3 DAY AS iv, "
        "'dbefa37c-f382-482a-8207-c53700538143'::UUID AS u, '{\"a\": 1}'::JSON AS j, "
        "12.34::DECIMAL(10, 2) AS d, 12345::HUGEINT AS h, '101'::BIT AS b, true AS flag, "
        "ST_Point(0, 0) AS geom")
    out = str(tmp_path / "o.parquet")
    sink_archive._copy_geoparquet(con, "v", out)
    row = con.execute(f"SELECT typeof(iv), iv, typeof(u), u::VARCHAR, typeof(j), typeof(d), d, "
                      f"typeof(h), h, b, typeof(flag) FROM '{out}'").fetchone()
    assert row == ("VARCHAR", "3 days", "UUID", "dbefa37c-f382-482a-8207-c53700538143", "JSON",
                   "DECIMAL(10,2)", Decimal("12.34"), "DOUBLE", 12345.0, "101", "BOOLEAN")
