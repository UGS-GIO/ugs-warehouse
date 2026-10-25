"""After an ingest changes a layer, its FeatureServer paths are invalidated on the Cloud CDN. Only
the HTTP session is faked."""
from __future__ import annotations

import sys
from types import SimpleNamespace

import google.auth
import google.auth.transport.requests as gtr
import pytest

from ugs_warehouse.core import cdn, config, jobs, stac
from ugs_warehouse.vector import ingest
from ugs_warehouse.vector.topics import Topic

API = ("https://compute.googleapis.com/compute/v1/projects/maps-prod/global/urlMaps/"
       "public-lb/invalidateCache")


@pytest.fixture
def posts(monkeypatch) -> list:
    """Each invalidateCache call as (url, rule); a path containing `bad` answers 403."""
    sent: list = []

    class Session:
        def __init__(self, creds):
            pass

        def post(self, url, json, timeout):
            sent.append((url, json))

            def raise_for_status():
                if "bad" in json["path"]:
                    raise RuntimeError("403")
            return SimpleNamespace(raise_for_status=raise_for_status)

    monkeypatch.setattr(google.auth, "default", lambda scopes: (object(), "p"))
    monkeypatch.setattr(gtr, "AuthorizedSession", Session)
    monkeypatch.setattr(cdn, "URL_MAP", "public-lb")
    monkeypatch.setattr(cdn, "PROJECT", "maps-prod")
    monkeypatch.setattr(cdn, "HOST", "api.geology.utah.gov")
    monkeypatch.setattr(cdn, "DELAY_S", 0)
    return sent


def _paths(posts: list) -> list[str]:
    return [rule["path"] for _, rule in posts]


def test_invalidates_every_render_of_the_layer(posts):
    cdn.invalidate_features(["hazards_qfaults"])
    assert posts == [(API, {"path": "/ugs/rest/services/hazards_qfaults/*",
                            "host": "api.geology.utah.gov"})]


@pytest.mark.parametrize("unset", ["URL_MAP", "PROJECT"])
def test_unconfigured_invalidates_nothing(posts, monkeypatch, unset):
    monkeypatch.setattr(cdn, unset, "")
    cdn.invalidate_features(["hazards_qfaults"])
    assert posts == []


def test_the_review_catalog_invalidates_nothing(posts, monkeypatch):
    monkeypatch.setattr(config, "IS_REVIEW_CATALOG", True)
    cdn.invalidate_features(["hazards_qfaults"])
    assert posts == []


def test_a_failed_path_still_tries_the_rest(posts):
    with pytest.raises(RuntimeError, match="bad"):
        cdn.invalidate_features(["bad", "hazards_qfaults"])
    assert _paths(posts) == ["/ugs/rest/services/bad/*", "/ugs/rest/services/hazards_qfaults/*"]


def _main(monkeypatch, *argv: str) -> int:
    monkeypatch.setattr(sys, "argv", ["ugs_warehouse.vector.ingest", *argv])
    return ingest.main()


def _changes(topic, changed=None, **kw) -> int:
    changed.append(topic.stem)
    return 0


def test_a_failed_invalidation_fails_the_run_after_everything_else(posts, monkeypatch):
    order: list = []
    topics = [Topic.parse("hazards.bad_current"), Topic.parse("hazards.hazards_qfaults_current")]
    monkeypatch.setattr(ingest, "_backend", lambda: SimpleNamespace(discover=lambda: topics))
    monkeypatch.setattr(ingest, "ingest_topic", _changes)
    monkeypatch.setattr(stac, "refresh_catalog", lambda: order.append("refresh"))
    monkeypatch.setattr(jobs, "start_topic_thumbs", lambda item_ids=None: order.append("thumbs"))
    assert _main(monkeypatch, "--all") == 1
    assert order == ["refresh", "thumbs"]
    assert _paths(posts) == ["/ugs/rest/services/bad/*", "/ugs/rest/services/hazards_qfaults/*"]


def test_a_dry_run_invalidates_nothing(posts, monkeypatch):
    monkeypatch.setattr(ingest, "ingest_topic", _changes)
    assert _main(monkeypatch, "--topic", "hazards.hazards_qfaults_current", "--dry-run") == 0
    assert posts == []


@pytest.mark.parametrize("title, served_change", [("Q", False), ("Quaternary faults", True)])
def test_a_rewritten_item_reports_whether_it_changed(monkeypatch, title, served_change):
    """The datetime falls back to the clock, so it differs on every run and is not a change."""
    from ugs_warehouse.vector import sink_stac

    monkeypatch.setattr(sink_stac, "_assets", lambda *a: {})
    for fn in ("attach_renders", "attach_classification", "stamp_file_meta"):
        monkeypatch.setattr(sink_stac.stac, fn, lambda _i: None)

    def write(title: str) -> bool:
        monkeypatch.setattr(sink_stac, "_properties", lambda *a: {"title": title})
        return sink_stac.write(Topic.parse("hazards.hazards_qfaults_current"), None, "v",
                               bbox=[-114.0, 37.0, -109.0, 42.0], row_count=1)

    assert write("Q") is True
    assert write(title) is served_change
