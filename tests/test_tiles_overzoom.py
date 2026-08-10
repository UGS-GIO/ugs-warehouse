"""What a client gets when it asks for a tile deeper than we cached.

Our Esri descriptor advertises `maxzoom: 22` with `maxLOD: 14`. A client that honours maxLOD
overzooms by rescaling the last cached level and never asks for z15+; one that overzooms by
REQUESTING is still inside the range we published, and used to get `404 application/json` where
it asked for protobuf (#117). Esri's own services never error inside the declared range.

Hermetic: the STAC catalog and the go-pmtiles child are stubbed.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tiles"))

fastapi_testclient = pytest.importorskip("fastapi.testclient")
app_mod = pytest.importorskip("app")

TOPIC = "hazards_qfaults"
MAX_LOD = 14
_ITEMS = json.dumps({"items": [{"id": TOPIC, "assets": {"pmtiles": {"href": "x"}}}]}).encode()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(app_mod, "_get", lambda url: _ITEMS)

    def fake_upstream(path: str):
        z = int(path.rsplit("/", 3)[1])
        if z > MAX_LOD:                       # go-pmtiles: nothing cached this deep
            raise app_mod.HTTPException(404, f"upstream 404 for {path}")
        if z == MAX_LOD:                      # in range, but this tile holds no features
            return b"", 204, {}
        return b"\x1a\x0f", 200, {"Content-Type": "application/x-protobuf"}

    monkeypatch.setattr(app_mod, "_upstream", fake_upstream)
    app_mod._cache.clear()
    return fastapi_testclient.TestClient(app_mod.app)


def test_a_tile_we_have_is_protobuf(client):
    r = client.get(f"/tiles/{TOPIC}/8/48/96.mvt")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/x-protobuf")


def test_an_empty_tile_in_range_is_204(client):
    assert client.get(f"/tiles/{TOPIC}/{MAX_LOD}/3000/6000.mvt").status_code == 204


@pytest.mark.parametrize("z", [MAX_LOD + 1, 18, 22])
def test_overzoom_is_an_absent_tile_not_an_error(client, z):
    r = client.get(f"/tiles/{TOPIC}/{z}/1/1.mvt")
    assert r.status_code == 204, "a client asking past maxLOD is still inside our declared maxzoom"
    assert not r.content
    # The regression: JSON handed to something that asked for protobuf.
    assert "application/json" not in r.headers.get("content-type", "")


def test_the_esri_route_agrees(client):
    """Esri swaps y/x, but the answer past maxLOD has to be the same shape."""
    assert client.get(f"/rest/services/{TOPIC}/VectorTileServer/tile/18/1/1.pbf").status_code == 204


def test_the_versioned_route_agrees(client):
    assert client.get(f"/tiles/{TOPIC}/v1/18/1/1.mvt").status_code == 204


def test_an_unknown_topic_still_404s(client):
    """Overzoom is absent; a topic we don't serve is a real error and must stay one."""
    assert client.get("/tiles/not_a_topic/18/1/1.mvt").status_code == 404
