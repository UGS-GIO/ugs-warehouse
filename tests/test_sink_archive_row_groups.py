"""The GeoParquet COPY must bound its row group size.

A row group is the smallest unit a range-reading client can fetch, so it sets the floor on the
viewer's first page. DuckDB's default (122,880 rows) put wetlands_riverine in two groups and made
an unsorted 50-row page pull 11.3 MB of column chunks.
"""
from __future__ import annotations

from ugs_warehouse.vector import sink_archive


class _FakeCon:
    def __init__(self) -> None:
        self.sql = ""

    def execute(self, sql: str) -> None:
        self.sql = sql


def test_copy_bounds_the_row_group_size() -> None:
    con = _FakeCon()
    sink_archive._copy_geoparquet(con, "v", "/tmp/out.parquet")
    assert f"ROW_GROUP_SIZE {sink_archive.ROW_GROUP_SIZE}" in con.sql


def test_row_group_size_stays_small_enough_to_page() -> None:
    # Above ~20k rows a first page stops being interactive; below ~5k the footer grows for nothing.
    assert 5_000 <= sink_archive.ROW_GROUP_SIZE <= 20_000


def test_copy_still_writes_the_bbox_covering_columns() -> None:
    con = _FakeCon()
    sink_archive._copy_geoparquet(con, "v", "/tmp/out.parquet")
    for col in ("bbox_xmin", "bbox_ymin", "bbox_xmax", "bbox_ymax"):
        assert col in con.sql
