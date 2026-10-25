"""A job that changes a layer marks it pending, and after a catalog refresh invalidates the
FeatureServer paths of every pending layer on the Cloud CDN. Only the HTTP session is faked; the
markers live in the in-memory bucket."""
from __future__ import annotations

import sys
from types import SimpleNamespace

import google.auth
import google.auth.credentials
import google.auth.transport.requests as gtr
import pytest

from ugs_warehouse.core import cdn, config, gcs, jobs, stac
from ugs_warehouse.vector import ingest
from ugs_warehouse.vector.topics import Topic

API = ("https://compute.googleapis.com/compute/v1/projects/maps-prod/global/urlMaps/"
       "public-lb/invalidateCache")
QFAULTS = "/ugs/rest/services/hazards_qfaults/*"


@pytest.fixture
def posts(monkeypatch) -> list:
    """Each invalidateCache call as (url, rule); a path containing `bad` fails."""
    sent: list = []

    class Session:
        def __init__(self, creds):
            pass

        def mount(self, prefix, adapter):
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


def _pending() -> list[str]:
    return sorted(p.rsplit("/", 1)[-1] for p in gcs.list_paths(f"{cdn.PENDING_PREFIX}/"))


def test_flush_invalidates_the_layer_list_and_every_pending_layer(posts):
    cdn.mark(["hazards_qfaults"])
    assert cdn.flush() == ["hazards_qfaults"]
    assert posts == [(API, {"path": "/", "host": "api.geology.utah.gov"}),
                     (API, {"path": QFAULTS, "host": "api.geology.utah.gov"})]
    assert _pending() == []


def test_nothing_pending_sends_nothing(posts, monkeypatch):
    monkeypatch.setattr(cdn.time, "sleep", lambda s: pytest.fail("waited with nothing pending"))
    assert cdn.flush() == []
    assert posts == []


@pytest.mark.parametrize("unset", ["URL_MAP", "PROJECT"])
def test_unconfigured_marks_and_sends_nothing(posts, monkeypatch, unset):
    monkeypatch.setattr(cdn, unset, "")
    cdn.mark(["hazards_qfaults"])
    cdn.flush()
    assert posts == [] and _pending() == []


def test_the_review_catalog_marks_and_sends_nothing(posts, monkeypatch):
    monkeypatch.setattr(config, "IS_REVIEW_CATALOG", True)
    cdn.mark(["hazards_qfaults"])
    cdn.flush()
    assert posts == [] and _pending() == []


def test_a_failed_path_still_tries_the_rest_and_keeps_every_marker(posts):
    cdn.mark(["bad", "hazards_qfaults"])
    with pytest.raises(RuntimeError, match="bad"):
        cdn.flush()
    assert _paths(posts) == ["/", "/ugs/rest/services/bad/*", QFAULTS]
    assert _pending() == ["bad", "hazards_qfaults"]


def test_a_layer_marked_again_during_the_wait_stays_pending(posts, monkeypatch):
    cdn.mark(["hazards_qfaults"])
    monkeypatch.setattr(cdn.time, "sleep", lambda s: cdn.mark(["hazards_qfaults"]))
    cdn.flush()
    assert _pending() == ["hazards_qfaults"]


def test_throttling_and_server_errors_are_retried(monkeypatch):
    monkeypatch.setattr(google.auth, "default",
                        lambda scopes: (google.auth.credentials.AnonymousCredentials(), "p"))
    retry = cdn._session().adapters["https://"].max_retries
    assert {429, 503} <= set(retry.status_forcelist) and retry.allowed_methods is None
    assert retry.total >= 3 and retry.backoff_factor > 0


def test_the_layer_fields_are_what_the_featureserver_serves():
    item = {"properties": {"title": "Q", "datetime": "2026-01-01T00:00:00Z",
                           "ugs:renders": {"default": {"title": "Faults", "esri_url": "e.json",
                                                       "legend": [1]}}},
            "assets": {"data": {"href": "d.parquet", "file:size": 1},
                       "overview_1": {"href": "o.parquet", "ugs:simplify_tolerance": 9.0},
                       "thumbnail": {"href": "t.webp"}}}
    same = {**item, "properties": {**item["properties"], "datetime": "2026-02-01T00:00:00Z"},
            "assets": {**item["assets"], "thumbnail": {"href": "t2.webp", "file:checksum": "x"}}}
    assert cdn.feature_fields(same) == cdn.feature_fields(item)
    restyled = {**item, "properties": {**item["properties"], "ugs:renders": {
        "default": {"title": "Faults", "esri_url": "e2.json"}}}}
    assert cdn.feature_fields(restyled) != cdn.feature_fields(item)


def test_a_rewritten_item_reports_whether_the_featureserver_sees_a_change(monkeypatch):
    from ugs_warehouse.vector import sink_stac

    monkeypatch.setattr(sink_stac, "_assets", lambda *a: {})
    for fn in ("attach_renders", "attach_classification", "stamp_file_meta"):
        monkeypatch.setattr(sink_stac.stac, fn, lambda _i: None)

    def write(title: str) -> bool:
        monkeypatch.setattr(sink_stac, "_properties", lambda *a: {"title": title})
        return sink_stac.write(Topic.parse("hazards.hazards_qfaults_current"), None, "v",
                               bbox=[-114.0, 37.0, -109.0, 42.0], row_count=1)

    assert write("Q") is True
    assert write("Q") is False  # a new clock datetime alone
    assert write("Quaternary faults") is True


# --- the ingest's flush ---------------------------------------------------------------------

def _main(monkeypatch, *argv: str, refresh_fails: bool = False) -> tuple[int, list]:
    order: list = []
    topics = [Topic.parse("hazards.bad_current"), Topic.parse("hazards.hazards_qfaults_current")]

    def ingest_topic(topic, **kw):
        cdn.mark([topic.stem])
        return 0

    def refresh():
        order.append("refresh")
        if refresh_fails:
            raise RuntimeError("refresh")

    monkeypatch.setattr(ingest, "_backend", lambda: SimpleNamespace(discover=lambda: topics))
    monkeypatch.setattr(ingest, "ingest_topic", ingest_topic)
    monkeypatch.setattr(stac, "refresh_catalog", refresh)
    monkeypatch.setattr(jobs, "start_topic_thumbs", lambda item_ids=None: order.append("thumbs"))
    monkeypatch.setattr(sys, "argv", ["ugs_warehouse.vector.ingest", *argv])
    return ingest.main(), order


def test_a_failed_invalidation_fails_the_run_after_everything_else(posts, monkeypatch):
    rc, order = _main(monkeypatch, "--all")
    assert rc == 1
    assert order == ["refresh", "thumbs"]
    assert _paths(posts) == ["/", "/ugs/rest/services/bad/*", QFAULTS]


@pytest.mark.parametrize("argv, refresh_fails", [(("--all",), True),
                                                 (("--all", "--skip-refresh"), False),
                                                 (("--topic", "hazards.hazards_qfaults_current",
                                                   "--skip-refresh"), False)])
def test_without_a_refresh_the_layers_stay_pending_for_the_next_run(posts, monkeypatch, argv,
                                                                    refresh_fails):
    rc, _ = _main(monkeypatch, *argv, refresh_fails=refresh_fails)
    assert rc == (1 if refresh_fails else 0)
    assert posts == []
    assert "hazards_qfaults" in _pending()


def test_the_next_run_invalidates_what_an_earlier_run_left_pending(posts, monkeypatch):
    cdn.mark(["left_over"])
    rc, order = _main(monkeypatch, "--topic", "hazards.hazards_qfaults_current")
    assert rc == 0 and order == ["refresh", "thumbs"]
    assert _paths(posts) == ["/", "/ugs/rest/services/hazards_qfaults/*",
                             "/ugs/rest/services/left_over/*"]
    assert _pending() == []


def test_a_dry_run_invalidates_nothing(posts, monkeypatch):
    cdn.mark(["left_over"])
    rc, order = _main(monkeypatch, "--topic", "hazards.hazards_qfaults_current", "--dry-run")
    assert rc == 0 and order == [] and posts == []
