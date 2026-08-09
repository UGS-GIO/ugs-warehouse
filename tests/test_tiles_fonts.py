"""Where a label layer gets its glyphs.

Labels are all-or-nothing in MapLibre: text layers whose `glyphs` fetch fails render nothing,
silently, which is how #116 shipped. ugs-styles now publishes the fontstacks and names them in
`ugs:renders`; these check the two hops that carry that through to a client.

Hermetic: the STAC catalog, the CDN and the go-pmtiles child are stubbed.
"""
from __future__ import annotations

import json
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tiles"))

fastapi_testclient = pytest.importorskip("fastapi.testclient")
app_mod = pytest.importorskip("app")

TOPIC = "enmin_ut_counties"
CDN_GLYPHS = "https://styles.test/fonts/{fontstack}/{range}.pbf"
STACK = "Noto Sans Regular"
_STYLE_FRAGMENT = json.dumps({"layers": [
    {"id": "labels", "type": "symbol",
     "layout": {"text-field": "{name}", "text-font": [STACK]}},
]}).encode()
_ITEMS = json.dumps({"items": [
    {"id": TOPIC, "assets": {"pmtiles": {"href": "x"}},
     "properties": {"ugs:renders": {"default": {
         "style_url": "https://styles.test/a.json", "glyphs": CDN_GLYPHS}}}},
]}).encode()


@pytest.fixture
def client(monkeypatch):
    def fake_get(url: str) -> bytes:
        if url.endswith("items.json"):
            return _ITEMS
        if url.endswith("/a.json"):
            return _STYLE_FRAGMENT
        if "/fonts/" in url:
            if f"{STACK.replace(' ', '%20')}/0-255" in url:
                return b"glyphs"
            raise urllib.error.HTTPError(url, 404, "not found", {}, None)
        return json.dumps({"properties": {"ugs:content_hash": "h"}}).encode()

    def fake_upstream(path: str):
        topic = path.strip("/").split("/")[0]
        return json.dumps({"vector_layers": [{"id": topic, "minzoom": 0, "maxzoom": 14}]}).encode(), 200, {}

    monkeypatch.setattr(app_mod, "_get", fake_get)
    monkeypatch.setattr(app_mod, "_upstream", fake_upstream)
    app_mod._cache.clear()
    return fastapi_testclient.TestClient(app_mod.app)


def test_style_carries_the_render_s_glyphs(client):
    assert client.get(f"/styles/{TOPIC}.json").json()["glyphs"] == CDN_GLYPHS


def test_style_falls_back_when_the_render_names_none(client, monkeypatch):
    """A fragment published before ugs-styles bound glyphs still has to draw its labels."""
    monkeypatch.setitem(app_mod._topics()[TOPIC]["properties"]["ugs:renders"]["default"], "glyphs", "")
    app_mod._cache.pop(f"style:{TOPIC}:default", None)
    assert client.get(f"/styles/{TOPIC}.json").json()["glyphs"] == app_mod.GLYPHS_URL


def test_esri_style_keeps_fonts_under_the_service(client):
    """Esri reads glyphs from the service; a URL that leaves it is one more thing Pro can be
    blocked from fetching."""
    doc = client.get(f"/rest/services/{TOPIC}/VectorTileServer/resources/styles/root.json").json()
    assert doc["glyphs"].endswith(
        f"/rest/services/{TOPIC}/VectorTileServer/resources/fonts/{{fontstack}}/{{range}}.pbf")

    served = client.get(doc["glyphs"].replace("{fontstack}", STACK).replace("{range}", "0-255"))
    assert served.status_code == 200
    assert served.content == b"glyphs"


def test_esri_font_route_passes_the_cdn_s_404_through(client):
    r = client.get(f"/rest/services/{TOPIC}/VectorTileServer/resources/fonts/Arial/0-255.pbf")
    assert r.status_code == 404


def test_esri_font_route_404s_an_unknown_topic(client):
    r = client.get(f"/rest/services/nope/VectorTileServer/resources/fonts/{STACK}/0-255.pbf")
    assert r.status_code == 404
