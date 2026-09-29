"""Only a topic's published version may restart go-pmtiles.

The tiles service is public and the version is just a path segment, so if any unseen string counted
as a re-ingest, a loop of made-up versions would restart the tile server every few seconds.

Hermetic: the STAC catalog, the go-pmtiles child and the restart are stubbed.
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

TOPIC = "hazards_qfaults"
_ITEMS = json.dumps({"items": [{"id": TOPIC, "assets": {"pmtiles": {"href": "x"}}}]}).encode()


@pytest.fixture
def state(monkeypatch):
    item = {"properties": {"ugs:content_hash": "sha256:v1"}}
    fetches = []

    def fake_get(url):
        fetches.append(url)
        if url.endswith("items.json"):
            return _ITEMS
        if item.get("cdn_down"):
            raise urllib.error.HTTPError(url, 503, "unavailable", {}, None)
        return json.dumps(item).encode()

    restarts = []
    monkeypatch.setattr(app_mod, "_get", fake_get)
    monkeypatch.setattr(app_mod, "_upstream",
                        lambda path: (b"\x1a\x0f", 200, {"Content-Type": "application/x-protobuf"}))
    monkeypatch.setattr(app_mod, "_restart_child", restarts.append)
    monkeypatch.setattr(app_mod, "_cache", {})
    monkeypatch.setattr(app_mod, "_served", {})
    monkeypatch.setattr(app_mod, "_rechecked", {})
    client = fastapi_testclient.TestClient(app_mod.app)
    return client, item, restarts, fetches


def _current():
    return app_mod._version(TOPIC)


def test_unknown_topic_is_404_and_never_restarts(state):
    client, _, restarts, _ = state
    assert client.get("/tiles/not_a_topic/abc/1/1/1.mvt").status_code == 404
    assert restarts == []


def test_made_up_versions_never_restart(state):
    client, _, restarts, fetches = state
    client.get(f"/tiles/{TOPIC}/{_current()}/1/1/1.mvt")
    before = len(fetches)
    for junk in ("a", "b", "c", "d"):
        r = client.get(f"/tiles/{TOPIC}/{junk}/1/1/1.mvt")
        assert r.status_code == 200
        assert "immutable" not in r.headers["cache-control"]
    assert restarts == []
    assert len(fetches) - before <= 1  # one recheck per VERSION_RECHECK window, not one per request


def test_a_real_reingest_still_restarts(state):
    client, item, restarts, _ = state
    old = _current()
    assert "immutable" in client.get(f"/tiles/{TOPIC}/{old}/1/1/1.mvt").headers["cache-control"]
    item["properties"]["ugs:content_hash"] = "sha256:v2"  # re-ingested; our cached version is stale
    new = app_mod.hashlib.sha1(b"sha256:v2").hexdigest()[:12]
    r = client.get(f"/tiles/{TOPIC}/{new}/1/1/1.mvt")
    assert "immutable" in r.headers["cache-control"]
    assert len(restarts) == 1


def test_a_new_version_inside_the_recheck_window_is_only_briefly_cached(state):
    client, item, restarts, _ = state
    client.get(f"/tiles/{TOPIC}/{_current()}/1/1/1.mvt")
    client.get(f"/tiles/{TOPIC}/junk/1/1/1.mvt")            # spends this window's recheck
    item["properties"]["ugs:content_hash"] = "sha256:v2"
    new = app_mod.hashlib.sha1(b"sha256:v2").hexdigest()[:12]
    r = client.get(f"/tiles/{TOPIC}/{new}/1/1/1.mvt")
    assert r.headers["cache-control"] == f"public, max-age={app_mod.VERSION_RECHECK:.0f}"
    assert restarts == []                                  # picked up at the next recheck


def test_a_cdn_blip_keeps_the_last_good_version(state):
    client, item, restarts, _ = state
    good = _current()
    item["cdn_down"] = True
    client.get(f"/tiles/{TOPIC}/junk/1/1/1.mvt")            # forces a recheck during the outage
    assert _current() == good                              # not "0", which would mint new URLs
    assert restarts == []


def test_no_version_yet_and_cdn_down_serves_unversioned_not_500(state):
    client, item, restarts, _ = state
    item["cdn_down"] = True
    assert app_mod._version(TOPIC) == app_mod.UNVERSIONED
    r = client.get(f"/tiles/{TOPIC}/{app_mod.UNVERSIONED}/1/1/1.mvt")
    assert r.status_code == 200
    assert "immutable" not in r.headers["cache-control"]
    assert restarts == []
    item["cdn_down"] = False
    assert app_mod._version(TOPIC) != app_mod.UNVERSIONED  # the failure wasn't cached
