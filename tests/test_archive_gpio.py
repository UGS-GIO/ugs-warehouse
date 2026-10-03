"""The archive GeoParquet passes geoparquet-io's checks: spec, bbox covering, compression, row
groups and spatial order. gpio is a dev dependency; the test skips without it."""
from __future__ import annotations

import pytest

from ugs_warehouse.vector import sink_archive

duckdb = pytest.importorskip("duckdb")
gpio = pytest.importorskip("geoparquet_io")


def _con():
    con = duckdb.connect()
    try:
        con.execute("LOAD spatial;")
    except duckdb.Error:
        con.execute("INSTALL spatial; LOAD spatial;")
    con.execute("CREATE TABLE t AS SELECT i AS id, 'unit ' || i AS name, -112 + (i % 200) / 200.0 AS x, "
                "39 + (i // 200) / 100.0 AS y FROM range(20000) r(i)")
    # The transform hands the sink a Hilbert-ordered view; this stands in for it.
    con.execute("CREATE VIEW v AS SELECT id, name, ST_Buffer(ST_Point(x, y), 0.002) AS geom "
                "FROM t ORDER BY ST_Hilbert(ST_Point(x, y))")
    return con


def test_the_archive_passes_gpio_check(tmp_path):
    path = str(tmp_path / "a.parquet")
    sink_archive._copy_geoparquet(_con(), "v", path)
    result = gpio.read(path).check()
    assert result.passed(), result.failures()


def test_gpio_check_fails_without_the_bbox_covering(tmp_path):
    path = str(tmp_path / "bare.parquet")
    _con().execute(f"COPY (SELECT id, name, geom FROM v) TO '{path}' (FORMAT PARQUET)")
    assert not gpio.read(path).check().passed()
