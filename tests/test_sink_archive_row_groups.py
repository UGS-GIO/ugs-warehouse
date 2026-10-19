"""The archive GeoParquet must bound its row group size.

A row group is the smallest unit a range-reading client can fetch, so it sets the floor on the
viewer's first page. DuckDB's default (122,880 rows) put wetlands_riverine in two groups and made
an unsorted 50-row page pull 11.3 MB of column chunks.
"""
from __future__ import annotations

from ugs_warehouse.vector import sink_archive


class _FakeCon:
    """Answers the bytes-per-row sample with `per_row`."""

    def __init__(self, per_row: float) -> None:
        self._per_row = per_row

    def execute(self, sql: str):
        return self

    def fetchone(self):
        return (self._per_row,)


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


def _written(tmp_path, rows: int = 5000):
    import duckdb
    import pyarrow.parquet as pq

    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial;")
    con.execute(f"CREATE VIEW v AS SELECT i AS id, 'unit ' || i AS name, "
                f"ST_Buffer(ST_Point(i % 100, i // 100), 0.4) AS geom FROM range({rows}) s(i)")
    path = str(tmp_path / "out.parquet")
    sink_archive._copy_geoparquet(con, "v", path)
    return sink_archive._row_group_size(con, "v"), pq.ParquetFile(path)


def test_copy_bounds_the_row_group_size(tmp_path) -> None:
    rows_per_group, f = _written(tmp_path)
    groups = [f.metadata.row_group(i).num_rows for i in range(f.metadata.num_row_groups)]
    assert max(groups) <= rows_per_group and sum(groups) == 5000


def test_copy_still_writes_the_bbox_covering_columns(tmp_path) -> None:
    _, f = _written(tmp_path)
    for col in ("bbox_xmin", "bbox_ymin", "bbox_xmax", "bbox_ymax", "bbox"):
        assert col in f.schema_arrow.names


def test_copy_writes_a_page_index_and_no_geometry_dictionary(tmp_path) -> None:
    """What lets a reader fetch only the pages a bbox touches inside a row group."""
    _, f = _written(tmp_path)
    geom = f.metadata.row_group(0).column(f.schema_arrow.names.index("geom"))
    assert geom.has_offset_index and geom.has_column_index
    assert not geom.has_dictionary_page
    assert b"geo" in f.schema_arrow.metadata


def _page_rows(path: str, column: str) -> list[int]:
    """Rows in each data page of `column`, read from the Thrift compact page headers."""
    import pyarrow.parquet as pq

    meta = pq.ParquetFile(path).metadata
    leaf = [meta.schema.column(i).path for i in range(meta.num_columns)].index(column)
    with open(path, "rb") as f:
        data = f.read()

    def varint(i):
        n = shift = 0
        while data[i] & 0x80:
            n |= (data[i] & 0x7F) << shift
            shift, i = shift + 7, i + 1
        return n | data[i] << shift, i + 1

    def struct(i):  # {field id: value}, for the field types a page header holds
        out, fid = {}, 0
        while data[i]:
            delta, kind = data[i] >> 4, data[i] & 0x0F
            fid, i = (fid + delta, i + 1) if delta else varint(i + 1)
            if kind == 12:
                out[fid], i = struct(i)
            elif kind in (5, 6):
                v, i = varint(i)
                out[fid] = (v >> 1) ^ -(v & 1)
            elif kind == 8:
                n, i = varint(i)
                i += n
        return out, i + 1

    rows = []
    for g in range(meta.num_row_groups):
        chunk = meta.row_group(g).column(leaf)
        assert chunk.has_offset_index and chunk.has_column_index
        i = chunk.data_page_offset
        while i < chunk.data_page_offset + chunk.total_compressed_size:
            header, i = struct(i)
            rows.append(header[5][1])
            i += header[3]
    return rows


def test_bbox_pages_are_short_enough_to_prune(tmp_path) -> None:
    """64 KiB pages of bbox doubles held 8192 rows; a z13 tile then read a page per row group."""
    _written(tmp_path)
    for col in ("bbox_xmin", "bbox_ymax", "bbox.xmin", "bbox.ymax"):
        rows = _page_rows(str(tmp_path / "out.parquet"), col)
        assert sum(rows) == 5000 and max(rows) <= sink_archive.PAGE_ROWS, (col, rows)


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


def test_is_current_accepts_only_the_format_write_makes(tmp_path) -> None:
    """Skip-unchanged rebuilds an archive from an older writer: GeoParquet 1.0 has no covering."""
    import duckdb

    from ugs_warehouse.core import config, gcs
    from ugs_warehouse.vector.topics import Topic

    topic = Topic(layer="hazards_rockfall_current", schema="hazards")
    assert not sink_archive.is_current(topic)  # nothing published yet

    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial")
    con.execute("CREATE TABLE v AS SELECT i AS feature_id, ST_Point(i, 40) AS geom FROM range(3) r(i)")
    old, new = str(tmp_path / "old.parquet"), str(tmp_path / "new.parquet")
    con.execute(f"COPY v TO '{old}' (FORMAT parquet)")  # DuckDB's own GeoParquet 1.0
    sink_archive._copy_geoparquet(con, "v", new)

    for path, current in ((old, False), (new, True)):
        with open(path, "rb") as f:
            gcs.put_bytes(f.read(), config.archive_path(topic.stem), content_type="application/x")
        assert sink_archive.is_current(topic) is current, path


def test_archive_signature_follows_the_writer_settings(monkeypatch) -> None:
    """A writer setting change makes every topic's fingerprint differ, so skip-unchanged rebuilds
    it with the new pages and overview levels."""
    before = sink_archive.archive_signature()
    monkeypatch.setattr(sink_archive, "PAGE_ROWS", sink_archive.PAGE_ROWS * 2)
    assert sink_archive.archive_signature() != before
    monkeypatch.undo()
    monkeypatch.setattr(sink_archive, "OVERVIEW_TOLERANCES", (0.001,))
    assert sink_archive.archive_signature() != before
