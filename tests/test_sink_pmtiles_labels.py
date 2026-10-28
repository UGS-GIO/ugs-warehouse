import json

import duckdb
import pytest

from ugs_warehouse.vector import sink_pmtiles
from ugs_warehouse.vector.topics import Topic

TOPIC = Topic(schema="emp", layer="enmin_ut_counties_current")
# An L: its centroid falls outside, in the missing corner.
L_SHAPE = "POLYGON((0 0, 10 0, 10 1, 1 1, 1 10, 0 10, 0 0))"


@pytest.fixture
def con():
    c = duckdb.connect()
    c.execute("LOAD spatial")
    return c


def _view(con, *wkts):
    rows = ", ".join(f"({i}, 'f{i}', ST_GeomFromText('{w}'))" for i, w in enumerate(wkts, 1))
    con.execute(f"CREATE OR REPLACE VIEW v AS SELECT * FROM (VALUES {rows}) t(feature_id, name, geom)")


def test_a_polygon_topic_tiles_a_labels_layer_after_its_own(con):
    _view(con, L_SHAPE, "POINT(5 5)")
    assert sink_pmtiles.tile_layers(TOPIC, con, "v") == ["enmin_ut_counties", "enmin_ut_counties_labels"]


def test_a_topic_without_polygons_tiles_only_its_own_layer(con):
    _view(con, "POINT(5 5)", "LINESTRING(0 0, 1 1)", "POLYGON EMPTY")
    assert sink_pmtiles.tile_layers(TOPIC, con, "v") == ["enmin_ut_counties"]


def test_a_topic_over_the_polygon_cap_gets_no_labels_layer(con, monkeypatch):
    monkeypatch.setattr(sink_pmtiles, "LABELS_MAX_POLYGONS", 1)
    _view(con, L_SHAPE, L_SHAPE)
    assert sink_pmtiles.tile_layers(TOPIC, con, "v") == ["enmin_ut_counties"]


def test_only_a_labelled_topic_moves_its_tiling_signature():
    plain = sink_pmtiles.tiling_signature(False)
    assert sink_pmtiles.tiling_signature(False, has_labels=False) == plain
    assert sink_pmtiles.tiling_signature(False, has_labels=True) != plain


def test_label_points_sit_inside_each_polygon_and_keep_its_attributes(con, tmp_path):
    _view(con, L_SHAPE, "POINT(50 50)", "MULTIPOLYGON(((20 20, 30 20, 30 30, 20 30, 20 20)))")
    path = tmp_path / "labels.geojsonl"
    sink_pmtiles.write_label_geojsonl(con, "v", str(path))
    features = [json.loads(line) for line in path.read_text().splitlines()]
    assert [f["properties"] for f in features] == [{"feature_id": 1, "name": "f1"},
                                                   {"feature_id": 3, "name": "f3"}]
    inside = con.execute(
        "SELECT bool_and(ST_Within(ST_GeomFromGeoJSON(l), v.geom)) FROM (VALUES (?, 1), (?, 3)) t(l, id) "
        "JOIN v ON v.feature_id = t.id",
        [json.dumps(f["geometry"]) for f in features],
    ).fetchone()[0]
    assert inside


def _cmd(monkeypatch, **kwargs):
    seen = []
    monkeypatch.setattr(sink_pmtiles.shutil, "which", lambda b: b)
    monkeypatch.setattr(sink_pmtiles.subprocess, "run", lambda cmd, **k: seen.append(cmd))
    sink_pmtiles.run_tippecanoe("main.geojsonl", "out.pmtiles", "lyr", **kwargs)
    return seen[0]


def test_one_layer_is_named_with_l(monkeypatch):
    cmd = _cmd(monkeypatch)
    assert cmd[3:5] == ["-l", "lyr"] and cmd[-1] == "main.geojsonl" and "-L" not in cmd


def test_extra_layers_name_every_input_with_L_instead_of_merging_them(monkeypatch):
    cmd = _cmd(monkeypatch, extra_layers=[("lyr_labels", "labels.geojsonl")])
    assert "-l" not in cmd
    assert cmd[-4:] == ["-L", "lyr:main.geojsonl", "-L", "lyr_labels:labels.geojsonl"]
