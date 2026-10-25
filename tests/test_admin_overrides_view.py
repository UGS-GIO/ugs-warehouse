"""Saving a serving topic's override in the ops console starts the catalog refresh job, whose flush
invalidates the topic on the CDN."""
from __future__ import annotations

import os
import sys

import pytest

django = pytest.importorskip("django")

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../admin")))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("ALLOWED_HOSTS", "testserver,localhost")
from django.apps import apps  # noqa: E402

if not apps.ready:
    django.setup()

from django.test import Client, override_settings  # noqa: E402
from ops import jobs, overrides  # noqa: E402

from ugs_warehouse import iap  # noqa: E402


@pytest.fixture()
def client(monkeypatch, fake_iap):
    fake_iap.install(monkeypatch, iap)
    return Client(HTTP_X_GOOG_IAP_JWT_ASSERTION=fake_iap.token("geologist@utah.gov"))


@pytest.fixture()
def started(monkeypatch) -> list:
    calls: list = []
    monkeypatch.setattr(jobs, "run_catalog_refresh", lambda item_id: calls.append(item_id)
                        or {"ok": True, "message": "started"})
    return calls


def test_a_serving_topic_override_starts_the_catalog_refresh(client, started, monkeypatch):
    monkeypatch.setattr(overrides, "save", lambda *a: {"applied": True, "fields": ["title"],
                                                       "serving_topic": True})
    res = client.post("/overrides/hazards_qfaults/save", {"title": "Q"})
    assert res.status_code == 200
    assert started == ["hazards_qfaults"]
    assert b"CDN invalidation: started" in res.content


def test_a_publication_override_starts_nothing(client, started, monkeypatch):
    monkeypatch.setattr(overrides, "save", lambda *a: {"applied": True, "fields": ["title"]})
    client.post("/overrides/OFR-1/save", {"title": "Q"})
    assert started == []


@override_settings(JOBS_DRY_RUN=False)
def test_the_refresh_job_is_told_which_topic_to_invalidate(monkeypatch):
    sent: list = []
    monkeypatch.setattr(jobs, "_start", lambda job, msg, args, **kw: sent.append((job.name, args)))
    jobs.run_catalog_refresh("hazards_qfaults")
    assert sent == [("ugs-warehouse-restyle",
                     ["-m", "scripts.refresh_stac", "--mark", "hazards_qfaults"])]
