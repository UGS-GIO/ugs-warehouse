"""Heavy line and polygon layers get one tile-ready GeoParquet level per web map zoom: GeoParquet
1.1 in EPSG:3857 with native GeoArrow geometry, every point on the zoom's pixel grid."""
from __future__ import annotations

import json
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from ugs_warehouse.core import gcs
from ugs_warehouse.vector import sink_archive, sink_stac
from ugs_warehouse.vector.topics import Topic

duckdb = pytest.importorskip("duckdb")

TOPIC = Topic("wetlands_nonriverine_current", "wetlands")
LODS = (9, 12)
# 120 polygons with holes around Salt Lake City, then three specks in one cell.
POLYGONS = ("SELECT i + 1 AS feature_id, 'w' || (i % 3) AS wetland_type, 'note ' || i AS note, CASE WHEN i < 120 "
            "THEN ST_Difference(ST_Buffer(ST_Point(-112 + (i % 12) * 0.05, 40.5 + (i // 12) * 0.05), 0.02, 64), "
            "ST_Buffer(ST_Point(-112 + (i % 12) * 0.05, 40.5 + (i // 12) * 0.05), 0.005, 16)) "
            "ELSE ST_Buffer(ST_Point(-111 + (i % 3) * 0.00001, 41), 0.000005, 2) END AS geom FROM range(123) r(i)")
LINES = ("SELECT i + 1 AS feature_id, 'f' AS wetland_type, ST_MakeLine([ST_Point(-112 + i * 0.01, 40), "
         "ST_Point(-112 + i * 0.01 + 0.004, 40.003), ST_Point(-112 + i * 0.01 + 0.008, 40)]) AS geom FROM range(50) r(i)")


@pytest.fixture
def uploads(monkeypatch, tmp_path):
    calls = {"upload": {}, "delete": [], "stored": [sink_archive.config.tile_level_path(TOPIC.stem, 5)]}

    def upload(local, path, **_):
        kept = tmp_path / path.replace("/", "_")
        kept.write_bytes(Path(local).read_bytes())
        calls["upload"][path] = str(kept)
        return gcs.FileMeta(kept.stat().st_size)

    monkeypatch.setattr(sink_archive.gcs, "upload", upload)
    monkeypatch.setattr(sink_archive.gcs, "list_paths",
                        lambda prefix: [p for p in calls["stored"] if p.startswith(prefix)])
    monkeypatch.setattr(sink_archive.gcs, "delete_many", lambda paths, **_: calls["delete"].extend(paths))
    monkeypatch.setattr(sink_archive, "OVERVIEW_MIN_VERTICES", 1)
    monkeypatch.setattr(sink_archive, "OVERVIEW_TOLERANCES", ())
    monkeypatch.setattr(sink_archive, "TILE_LODS", LODS)
    monkeypatch.setattr(sink_archive, "_merge_class", lambda item_id: None)
    monkeypatch.setattr(sink_archive.styles, "renders_for",
                        lambda item_id, keys: ({"default": {"field": "wetland_type"}}, None))
    return calls


def _write(sql: str, listed: set[str] = frozenset()) -> dict:
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial;")
    con.execute(f"CREATE VIEW v AS {sql}")
    written = {"data": gcs.FileMeta(10**9)}
    sink_archive.write_overviews(TOPIC, con, "v", written, listed)
    return written


def _level(uploads, lod: int):
    return pq.read_table(uploads["upload"][sink_archive.config.tile_level_path(TOPIC.stem, lod)])


def test_each_lod_resolution_is_map_viewer_s():
    assert sink_archive.lod_resolution(0) == pytest.approx(2 * 20037508.342789244 / 256)
    assert sink_archive.lod_resolution(13) == pytest.approx(19.109257071294063)


def test_a_level_is_geoparquet_1_1_in_web_mercator_with_native_geometry(uploads):
    written = _write(POLYGONS)
    assert {k for k in written if k.startswith("tiles_")} == {"tiles_lod09", "tiles_lod12"}
    table = _level(uploads, 12)
    geo = json.loads(table.schema.metadata[b"geo"])
    column = geo["columns"]["geometry"]
    assert geo["version"] == "1.1.0" and geo["primary_column"] == "geometry"
    assert column["encoding"] == "multipolygon" and column["geometry_types"] == ["MultiPolygon"]
    assert column["crs"]["id"] == {"authority": "EPSG", "code": 3857}
    assert column["covering"]["bbox"]["xmin"] == ["bbox", "xmin"]
    assert table.schema.metadata[b"ugs:lod"] == b"12"
    # feature_id and the style's field ride along; the other attributes stay in the archive
    assert table.column_names == ["feature_id", "wetland_type", "geometry", "bbox"]


def test_points_are_on_the_lod_grid_inside_the_covering_and_in_feature_order(uploads):
    _write(POLYGONS)
    step = sink_archive.lod_resolution(12)
    rows = _level(uploads, 12).to_pylist()
    ids = [r["feature_id"] for r in rows]
    assert ids == sorted(ids) and ids[:120] == list(range(1, 121))
    assert len(rows[0]["geometry"][0]) == 2  # the outer ring and its hole
    for r in rows:
        b = r["bbox"]
        for poly in r["geometry"]:
            for ring in poly:
                assert len(ring) >= 4 and ring[0] == ring[-1]
                assert all(a != b for a, b in zip(ring, ring[1:]))  # no point repeats the last
                for p in ring:
                    assert p["x"] / step == pytest.approx(round(p["x"] / step), abs=1e-6)
                    assert p["y"] / step == pytest.approx(round(p["y"] / step), abs=1e-6)
                    assert b["xmin"] <= p["x"] <= b["xmax"] and b["ymin"] <= p["y"] <= b["ymax"]


def test_a_coarser_level_has_fewer_points(uploads):
    _write(POLYGONS)

    def points(lod):
        return sum(len(ring) for r in _level(uploads, lod).to_pylist() for poly in r["geometry"] for ring in poly)

    assert points(9) < points(12)


def test_lines_are_multilinestrings(uploads):
    _write(LINES)
    table = _level(uploads, 12)
    column = json.loads(table.schema.metadata[b"geo"])["columns"]["geometry"]
    assert column["encoding"] == "multilinestring"
    assert all(len(line) >= 2 for r in table.to_pylist() for line in r["geometry"])


def test_a_layer_mixing_lines_and_polygons_gets_no_levels(uploads):
    written = _write(f"SELECT * FROM ({POLYGONS}) UNION ALL SELECT feature_id + 1000, wetland_type, 'x', geom FROM ({LINES})")
    assert not [k for k in written if k.startswith("tiles_")]


def test_an_unlisted_old_level_is_removed_and_a_listed_one_kept(uploads):
    old = sink_archive.config.tile_level_path(TOPIC.stem, 5)
    _write(POLYGONS)
    assert uploads["delete"] == [old]
    uploads["delete"].clear()
    _write(POLYGONS, listed={old})
    assert uploads["delete"] == []


def test_the_item_lists_the_levels_written(monkeypatch):
    monkeypatch.setattr(sink_stac.gcs, "exists", lambda p: True)
    monkeypatch.setattr(sink_stac, "_table_columns", lambda c, v: [])
    monkeypatch.setattr(sink_stac.stac, "prior_file_fields", lambda cp, iid: {"tiles_lod09": {}})
    monkeypatch.setattr(sink_archive, "TILE_LODS", LODS)
    written = {"data": gcs.FileMeta(9), "tiles_lod12": gcs.FileMeta(5)}
    assets = sink_stac._assets(TOPIC, None, "v", "x.pmtiles", {}, written)
    tiles = {k: a for k, a in assets.items() if k.startswith("tiles_")}
    assert set(tiles) == {"tiles_lod12"}
    assert tiles["tiles_lod12"]["ugs:lod"] == 12 and tiles["tiles_lod12"]["proj:code"] == "EPSG:3857"
    assert tiles["tiles_lod12"]["roles"] == ["ugs:tiles"]
    assert sink_stac.published_overviews(TOPIC) == {sink_archive.config.tile_level_path(TOPIC.stem, 9)}


def test_a_speck_is_one_pixel(uploads):
    _write(POLYGONS)
    step = sink_archive.lod_resolution(9)
    specks = [r for r in _level(uploads, 9).to_pylist() if r["feature_id"] > 120]
    assert len(specks) == 1  # one a cell, as the overviews keep
    for r in specks:
        (ring,), = r["geometry"]
        x, y = ring[0]["x"], ring[0]["y"]
        assert [(p["x"], p["y"]) for p in ring] == pytest.approx(
            [(x, y), (x + step, y), (x + step, y + step), (x, y)])
        assert r["bbox"] == pytest.approx({"xmin": x, "ymin": y, "xmax": x + step, "ymax": y + step})
