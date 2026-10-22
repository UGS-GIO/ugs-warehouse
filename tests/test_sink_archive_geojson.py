"""The archive sink writes a GeoJSON copy of a small layer, and the item lists it."""
from __future__ import annotations

import json

import pytest

from ugs_warehouse.core import gcs
from ugs_warehouse.vector import sink_archive, sink_stac
from ugs_warehouse.vector.topics import Topic

duckdb = pytest.importorskip("duckdb")

TOPIC = Topic("geolmap_strat_columns_current", "geolmap")
PATH = sink_archive.config.geojson_path(TOPIC.stem)
POINTS = ("SELECT i + 1 AS feature_id, 'c' || i AS name, DATE '2020-01-02' AS day, "
          "ST_Multi(ST_Point(-111.123456789 + i, 40)) AS geom FROM range(3) r(i)")


@pytest.fixture
def puts(monkeypatch):
    calls = {"put": {}, "delete": []}

    def put_bytes(data, path, **kw):
        calls["put"][path] = (data, kw)
        return gcs.FileMeta(len(data))

    monkeypatch.setattr(sink_archive.gcs, "put_bytes", put_bytes)
    monkeypatch.setattr(sink_archive.gcs, "delete_many", lambda paths: calls["delete"].extend(paths))
    return calls


def _write(archive_size: int, listed: bool = False) -> dict:
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial;")
    con.execute(f"CREATE VIEW v AS {POINTS}")
    written = {"data": gcs.FileMeta(archive_size)}
    sink_archive.write_geojson(TOPIC, con, "v", written, listed)
    return written


def test_a_small_layer_gets_a_gzipped_feature_collection_keyed_by_feature_id(puts):
    written = _write(1000)
    data, kw = puts["put"][PATH]
    assert written["geojson"].size == len(data)
    assert kw["compress"] and kw["content_type"] == "application/geo+json"
    features = json.loads(data)["features"]
    assert [f["id"] for f in features] == [1, 2, 3]
    assert features[0]["properties"] == {"name": "c0", "day": "2020-01-02"}
    assert features[0]["geometry"]["coordinates"] == [[-111.1234568, 40.0]]   # RFC 7946, 7 digits
    assert not puts["delete"]


def test_a_large_layer_gets_none_and_its_old_file_goes_once_unlisted(puts):
    assert "geojson" not in _write(sink_archive.GEOJSON_MAX_BYTES + 1, listed=True)
    assert not puts["delete"]
    _write(sink_archive.GEOJSON_MAX_BYTES + 1, listed=False)
    assert puts["delete"] == [PATH] and not puts["put"]


def _assets(monkeypatch, file_meta: dict, published: dict, present: bool) -> dict:
    monkeypatch.setattr(sink_stac.gcs, "exists", lambda p: present and p == PATH)
    monkeypatch.setattr(sink_stac, "_table_columns", lambda c, v: [])
    monkeypatch.setattr(sink_stac.stac, "prior_file_fields", lambda cp, iid: published)
    return sink_stac._assets(TOPIC, None, "v", "x.pmtiles", {}, file_meta)


def test_the_item_lists_the_geojson_written_with_the_archive(monkeypatch):
    written = {"data": gcs.FileMeta(9), "geojson": gcs.FileMeta(40)}
    asset = _assets(monkeypatch, written, {}, True)["geojson"]
    assert asset["type"] == "application/geo+json" and asset["roles"] == ["data"]
    assert asset["href"].endswith(PATH) and asset["file:size"] == 40
    assert "geojson" not in _assets(monkeypatch, {"data": gcs.FileMeta(9)}, {"geojson": {}}, True)


def test_an_unchanged_archive_keeps_the_published_geojson(monkeypatch):
    assert "geojson" in _assets(monkeypatch, {}, {"geojson": {"file:size": 40}}, True)
    assert "geojson" not in _assets(monkeypatch, {}, {"geojson": {"file:size": 40}}, False)
    assert sink_stac.published_geojson(TOPIC)
