"""The GeoParquet COPY must bound its row group size.

A row group is the smallest unit a range-reading client can fetch, so it sets the floor on the
viewer's first page. DuckDB's default (122,880 rows) put wetlands_riverine in two groups and made
an unsorted 50-row page pull 11.3 MB of column chunks.
"""
from __future__ import annotations

from ugs_warehouse.vector import sink_archive


class _FakeCon:
    """Records the COPY, and answers the bytes-per-row sample with `per_row`."""

    def __init__(self, per_row: float = 2_000.0) -> None:
        self.sql = ""
        self._per_row = per_row

    def execute(self, sql: str):
        if sql.lstrip().upper().startswith("SELECT"):
            return self
        self.sql = sql
        return self

    def fetchone(self):
        return (self._per_row,)

    def fetchall(self):
        return [("MULTIPOLYGON", False)]


def test_copy_bounds_the_row_group_size() -> None:
    con = _FakeCon()
    sink_archive._copy_geoparquet(con, "v", "/tmp/out.parquet")
    assert "ROW_GROUP_SIZE " in con.sql


def test_row_group_size_targets_bytes_not_rows() -> None:
    # The point of the bound is a group a clipped read can skip, and that is a size in bytes.
    heavy = sink_archive._row_group_size(_FakeCon(per_row=40_000), "v")
    light = sink_archive._row_group_size(_FakeCon(per_row=40), "v")
    assert heavy * 40_000 <= sink_archive.TARGET_ROW_GROUP_BYTES
    assert heavy < light
    assert sink_archive.ROW_GROUP_MIN <= heavy <= sink_archive.ROW_GROUP_MAX

    # wetlands_riverine: 1.55 GB of geometry over 147,506 rows went out as 2 groups, the larger
    # 1.06 GB. At its real weight the bound puts it in dozens.
    riverine = sink_archive._row_group_size(_FakeCon(per_row=1.55 * 1024**3 / 147_506), "v")
    assert 147_506 / riverine > 20, f"{riverine} rows/group still leaves too few groups"


def test_copy_still_writes_the_bbox_covering_columns() -> None:
    con = _FakeCon()
    sink_archive._copy_geoparquet(con, "v", "/tmp/out.parquet")
    for col in ("bbox_xmin", "bbox_ymin", "bbox_xmax", "bbox_ymax"):
        assert col in con.sql


# A row group is the floor on what a clipped export downloads, so it has to be bounded in BYTES.
# A fixed row count is not: a row's weight is mostly its geometry, and that varies by three orders
# of magnitude. wetlands_riverine went out in 2 groups, the larger 1.06 GB, so a 22-feature AOI
# still read the whole file.
def test_row_group_size_shrinks_for_heavy_geometry(tmp_path):
    import duckdb

    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial;")
    # ~40 KB of polyline per row, against a 32 MB target.
    con.execute(
        "CREATE VIEW heavy AS SELECT i AS id, "
        "ST_MakeLine(list_transform(range(2500), x -> ST_Point(x * 0.001, i * 0.001))) AS geom "
        "FROM range(200) s(i)"
    )
    heavy = sink_archive._row_group_size(con, "heavy")

    con.execute(
        "CREATE VIEW light AS SELECT i AS id, ST_Point(i % 100, i % 50) AS geom FROM range(5000) s(i)"
    )
    light = sink_archive._row_group_size(con, "light")

    assert heavy < light, f"heavy geometry should group fewer rows: {heavy} vs {light}"
    assert sink_archive.ROW_GROUP_MIN <= heavy <= sink_archive.ROW_GROUP_MAX
    assert sink_archive.ROW_GROUP_MIN <= light <= sink_archive.ROW_GROUP_MAX


def test_row_group_size_falls_back_when_the_view_is_empty():
    import duckdb

    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial;")
    con.execute("CREATE VIEW empty AS SELECT 1 AS id, ST_Point(0, 0) AS geom WHERE false")
    assert sink_archive._row_group_size(con, "empty") == sink_archive.ROW_GROUP_MAX


def test_copy_writes_geoparquet_1_1_with_a_bbox_covering(tmp_path) -> None:
    """1.1, not DuckDB's 1.0 or 2.0: 2.0 does not open in GDAL before 3.12 (every QGIS today)."""
    import json

    import duckdb

    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial")
    con.execute("CREATE TABLE v AS SELECT i AS feature_id, ST_Point(-112 + i / 100, 40) AS geom FROM range(3) r(i)")
    out = str(tmp_path / "o.parquet")
    sink_archive._copy_geoparquet(con, "v", out)

    geo = json.loads(con.execute(
        f"SELECT decode(value) FROM parquet_kv_metadata('{out}') WHERE decode(key) = 'geo'").fetchone()[0])
    assert geo["version"] == "1.1.0"
    assert geo["columns"]["geom"]["geometry_types"] == ["Point"]
    assert geo["columns"]["geom"]["covering"]["bbox"]["xmin"] == ["bbox", "xmin"]
    stats = con.execute(f"SELECT stats_min FROM parquet_metadata('{out}') "
                        f"WHERE path_in_schema = 'bbox, xmin'").fetchone()
    assert float(stats[0]) == -112.0
    assert con.execute(f"SELECT typeof(geom) FROM read_parquet('{out}') LIMIT 1").fetchone()[0].startswith("GEOMETRY")
