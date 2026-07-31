"""The Esri facade's PATH CONTRACT — the one thing here no other check can see.

`ruff` lints tiles/app.py; nothing imports it, so until this file existed the routes were covered
by no automated check at all. That is how the service shipped addressable only at `/esri/...`,
which ArcGIS Online rejects out of hand: AGOL matches the path against ArcGIS Server's REST layout
and gives up BEFORE issuing a request, so a flawless descriptor at the wrong path is never fetched.
Nothing in CI could have caught it, and nothing would catch a refactor putting it back.

Hermetic: the STAC catalog and the go-pmtiles child are both stubbed, so this runs offline.
"""
from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path
from urllib.parse import quote

import pytest

# tiles/ is a separate service, not part of the `src/` package — pythonpath in pyproject only
# covers src/, so point at it explicitly rather than restructuring the service to suit the tests.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tiles"))

fastapi_testclient = pytest.importorskip("fastapi.testclient")
app_mod = pytest.importorskip("app")

SINGLE = "solo_topic"          # one render named `default` -> a root-level service
MULTI = "multi_topic"          # two named renders          -> a folder of two services
BARE = "bare_topic"            # no published render        -> Esri cannot add it at all
RENDER = "by-purpose"
# Deliberately hostile, and REGISTERED so it survives `_pick_render` and reaches the URL builder —
# a name that only 404s tests nothing about encoding.
NASTY = "a?b c"

_STYLE_FRAGMENT = json.dumps({"layers": [{"id": "l0", "type": "line", "paint": {}}]}).encode()
_ITEMS = json.dumps({"items": [
    {"id": SINGLE, "assets": {"pmtiles": {"href": "x"}},
     "properties": {"ugs:renders": {"default": {"style_url": "https://styles.test/a.json"}}}},
    {"id": MULTI, "assets": {"pmtiles": {"href": "x"}},
     "properties": {"ugs:renders": {
         RENDER: {"style_url": "https://styles.test/b.json"},
         "by-boxtype": {"style_url": "https://styles.test/c.json"},
         NASTY: {"style_url": "https://styles.test/d.json"}}}},
    {"id": BARE, "assets": {"pmtiles": {"href": "x"}}, "properties": {}},
]}).encode()
_TILE = gzip.compress(b"mvt")


@pytest.fixture
def client(monkeypatch):
    def fake_get(url: str) -> bytes:
        if url.endswith("items.json"):
            return _ITEMS
        if url.startswith("https://styles.test/"):
            return _STYLE_FRAGMENT
        return json.dumps({"properties": {"ugs:content_hash": "h"}}).encode()  # per-item doc

    def fake_upstream(path: str):
        if path.endswith("/metadata"):
            topic = path.strip("/").split("/")[0]
            return json.dumps({"vector_layers": [{"id": topic, "minzoom": 0, "maxzoom": 14}],
                               "antimeridian_adjusted_bounds": "-114,37,-109,42"}).encode(), 200, {}
        return _TILE, 200, {"Content-Type": "application/x-protobuf", "Content-Encoding": "gzip"}

    monkeypatch.setattr(app_mod, "_get", fake_get)
    monkeypatch.setattr(app_mod, "_upstream", fake_upstream)
    app_mod._cache.clear()
    # Not `with TestClient(...)`: the context manager runs the lifespan, which spawns go-pmtiles.
    return fastapi_testclient.TestClient(app_mod.app)


# `/rest/services` is the contract; `/esri` is the alias kept alive for URLs copied out of the
# viewer before the move. Both must behave identically — parametrising is the point, not a shortcut.
BOTH = pytest.mark.parametrize("prefix", ["/rest/services", "/esri"])


@BOTH
def test_descriptor_served_under_both_prefixes(client, prefix):
    r = client.get(f"{prefix}/{SINGLE}/VectorTileServer")
    assert r.status_code == 200
    doc = r.json()
    assert doc["name"] == SINGLE
    # Both relative — Esri resolves them against the service URL, so they survive either mount.
    assert doc["tiles"] == ["tile/{z}/{y}/{x}.pbf"]
    assert doc["defaultStyles"] == "resources/styles"


@BOTH
def test_style_tile_url_carries_the_prefix_the_client_used(client, prefix):
    """The style names its tile URL absolutely, so a request that came in on one mount must not be
    handed URLs on the other — that would bounce an AGOL client back onto the unaddable path."""
    r = client.get(f"{prefix}/{SINGLE}/VectorTileServer/resources/styles/root.json")
    assert r.status_code == 200
    tiles = next(iter(r.json()["sources"].values()))["tiles"][0]
    assert tiles.endswith(f"{prefix}/{SINGLE}/VectorTileServer/tile/{{z}}/{{y}}/{{x}}.pbf")


@BOTH
def test_per_render_service_per_symbology(client, prefix):
    """Pro fetches the style with no query string, so `?render=` is unreachable — each symbology
    has to be its own service or only one of them is ever addressable."""
    r = client.get(f"{prefix}/{MULTI}/{RENDER}/VectorTileServer")
    assert r.status_code == 200
    assert r.json()["name"] == f"{MULTI} ({RENDER})"

    style = client.get(f"{prefix}/{MULTI}/{RENDER}/VectorTileServer/resources/styles/root.json")
    assert style.status_code == 200
    tiles = next(iter(style.json()["sources"].values()))["tiles"][0]
    assert tiles.endswith(f"{prefix}/{MULTI}/{RENDER}/VectorTileServer/tile/{{z}}/{{y}}/{{x}}.pbf")


@BOTH
def test_tiles_pass_gzip_through_unrecompressed(client, prefix):
    r = client.get(f"{prefix}/{SINGLE}/VectorTileServer/tile/8/97/48.pbf")
    assert r.status_code == 200
    assert r.headers["content-encoding"] == "gzip"


@BOTH
def test_unknown_topic_404s(client, prefix):
    assert client.get(f"{prefix}/nope/VectorTileServer").status_code == 404


def test_index_advertises_only_the_addable_form(client):
    """A URL under `/esri/` cannot be added in AGOL, so handing one out is a dead link."""
    urls = [u for c in client.get("/").json()["collections"] for u in c["arcgis"].values()]
    assert urls, "expected at least one arcgis URL"
    assert all("/rest/services/" in u for u in urls)
    assert not any("/esri/" in u for u in urls)


def test_rest_info_declares_anonymous_access(client):
    """The ArcGIS Server handshake. Portal/Pro read `authInfo` here to decide whether a URL needs a
    token; claiming the REST path shape without answering this leaves that question open."""
    r = client.get("/rest/info")
    assert r.status_code == 200
    assert r.json()["authInfo"]["isTokenBasedSecurity"] is False


def test_catalog_splits_folders_from_services(client):
    """A name is a folder or a service, never both — real ArcGIS catalogs never list it twice."""
    d = client.get("/rest/services").json()
    assert MULTI in d["folders"]                                    # renders live under it
    assert not any(s["name"] == MULTI for s in d["services"])
    assert {"name": SINGLE, "type": "VectorTileServer"} in d["services"]
    assert SINGLE not in d["folders"]
    # An Esri vector tile layer REQUIRES a style; listing a styleless topic advertises a click
    # that fails.
    assert BARE not in d["folders"]
    assert not any(s["name"] == BARE for s in d["services"])


def test_folder_lists_one_service_per_render(client):
    d = client.get(f"/rest/services/{MULTI}").json()
    assert [s["name"] for s in d["services"]] == [
        f"{MULTI}/{NASTY}", f"{MULTI}/by-boxtype", f"{MULTI}/{RENDER}"]
    assert client.get(f"/rest/services/{BARE}").status_code == 404
    assert client.get("/rest/services/nope").status_code == 404


def test_single_default_topic_is_a_service_not_a_folder(client):
    """It is listed as a bare service, so it must not also answer as a folder — otherwise the same
    layer is addressable two ways and AGOL ends up with two portal items for it."""
    assert client.get(f"/rest/services/{SINGLE}").status_code == 404


def test_index_omits_the_render_segment_for_a_single_default_topic(client):
    """Esri titles the layer from the URL's last segment, so `/default/` imports as "Default".
    The bare form also matches what the root catalog advertises."""
    arcgis = {c["id"]: c["arcgis"] for c in client.get("/").json()["collections"]}
    assert arcgis[SINGLE]["default"].endswith(f"/rest/services/{SINGLE}/VectorTileServer")
    assert arcgis[MULTI][RENDER].endswith(f"/rest/services/{MULTI}/{RENDER}/VectorTileServer")


def test_descriptor_404s_when_no_style_is_published(client):
    """Esri cannot add a styleless vector tile layer — it fails on the 404ing style resource. A
    200 here would hand out a URL that cannot work, and contradict the catalog, which omits it."""
    assert client.get(f"/rest/services/{BARE}/VectorTileServer").status_code == 404
    assert client.get(f"/esri/{BARE}/VectorTileServer").status_code == 404


def test_folder_listing_does_not_shadow_the_descriptor(client):
    """`/rest/services/{topic}` and `/rest/services/{topic}/VectorTileServer` differ by one
    segment; a greedy match on the former would swallow every descriptor."""
    assert "tileInfo" in client.get(f"/rest/services/{SINGLE}/VectorTileServer").json()
    assert "tileInfo" not in client.get(f"/rest/services/{MULTI}").json()


def test_special_characters_in_a_render_name_stay_encoded_in_the_tile_url(client):
    """ASGI percent-DECODES into scope["path"] before routing, so a render named `a?b c` arrives
    as literal text. Interpolated raw, everything after the `?` becomes a query string and the
    tile URL silently points nowhere — a 200, an empty map, nothing in any log."""
    r = client.get(
        f"/rest/services/{MULTI}/{quote(NASTY, safe='')}/VectorTileServer"
        "/resources/styles/root.json")
    assert r.status_code == 200
    tiles = next(iter(r.json()["sources"].values()))["tiles"][0]
    assert "a%3Fb%20c" in tiles, tiles
    assert "?" not in tiles and " " not in tiles, tiles

    # Same hazard on the advertised URL in the index.
    url = {c["id"]: c["arcgis"] for c in client.get("/").json()["collections"]}[MULTI][NASTY]
    assert "a%3Fb%20c" in url, url
    assert "?" not in url and " " not in url, url
