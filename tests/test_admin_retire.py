"""Ops-console retire flow: the dispatch args, and the confirmation the console cannot leave to JS.

The button that submits step 2 is disabled until the operator types the topic back, but a disabled
button is a hint, not a control — the endpoint is reachable without it. These pin the server-side
check, the argument shapes the CLI is trusted to receive, and the execution-scoped preview (a
job-wide log tail would show whatever ran before).
"""
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
from ops import jobs  # noqa: E402

from ugs_warehouse import iap  # noqa: E402

TOPIC = "hazards.hazards_qfaults_current"
OPERATOR = "geologist@utah.gov"


@pytest.fixture()
def client(monkeypatch, fake_iap):
    fake_iap.install(monkeypatch, iap)
    return Client(HTTP_X_GOOG_IAP_JWT_ASSERTION=fake_iap.token(OPERATOR))


@override_settings(DEBUG=False)
def test_a_forged_iap_email_header_gets_no_admin(monkeypatch, fake_iap, dispatched):
    fake_iap.install(monkeypatch, iap)
    forged = Client(HTTP_X_GOOG_AUTHENTICATED_USER_EMAIL=f"accounts.google.com:{OPERATOR}")
    res = forged.post("/retire/execute", {"topic": TOPIC, "confirm": TOPIC})
    assert res.status_code == 403
    assert dispatched == []


@override_settings(DEBUG=False)
def test_an_invalid_iap_token_gets_no_admin(monkeypatch, fake_iap, other_iap, rejected_token_kwargs,
                                            dispatched):
    fake_iap.install(monkeypatch, iap)
    for token in (fake_iap.token(OPERATOR, **rejected_token_kwargs), other_iap.token(OPERATOR)):
        res = Client(HTTP_X_GOOG_IAP_JWT_ASSERTION=token).post(
            "/retire/execute", {"topic": TOPIC, "confirm": TOPIC})
        assert res.status_code == 403
    assert dispatched == []


@pytest.fixture()
def dispatched(monkeypatch):
    """Capture what the views would send to Cloud Run instead of sending it."""
    calls = []

    def fake(topic, **kw):
        calls.append({"topic": topic, **kw})
        return {"ok": True, "message": f"started {topic}", "execution": "retire-abc12"}

    monkeypatch.setattr(jobs, "run_retire", fake)
    monkeypatch.setattr(jobs, "recent", lambda key, limit=5: [])
    return calls


def test_args_carry_the_flag_the_step_means():
    assert jobs.retire_args(TOPIC, dry_run=True) == ["--topic", TOPIC, "--dry-run"]
    assert jobs.retire_args(TOPIC, dry_run=False) == ["--topic", TOPIC, "--yes"]
    assert jobs.retire_args(TOPIC, dry_run=False, purge_overrides=True)[-1] == "--purge-overrides"
    # A preview must stay a preview: --purge-overrides alongside --dry-run would still delete nothing,
    # but the preview would then report a deletion the operator has not agreed to.
    assert "--purge-overrides" not in jobs.retire_args(TOPIC, dry_run=True, purge_overrides=True)


@override_settings(JOBS_DRY_RUN=True)
@pytest.mark.parametrize("topic", ["hazards", "hazards.hazards_qfaults", "hazards.évil_current"])
def test_a_topic_that_is_not_a_serving_table_never_reaches_cloud_run(topic):
    result = jobs.run_retire(topic, dry_run=False)
    assert result["ok"] is False
    assert "would execute" not in result["message"]


def test_execute_refuses_when_the_typed_name_does_not_match(client, dispatched):
    res = client.post("/retire/execute", {"topic": TOPIC, "confirm": "hazards_qfaults"})

    assert dispatched == []
    assert f"type {TOPIC} exactly" in res.content.decode()


def test_execute_runs_once_the_name_matches(client, dispatched):
    res = client.post("/retire/execute",
                      {"topic": TOPIC, "confirm": f"  {TOPIC}  ", "purge_overrides": "1"})

    assert dispatched == [{"topic": TOPIC, "dry_run": False, "purge_overrides": True,
                           "requested_by": OPERATOR}]
    assert "started" in res.content.decode()


def test_preview_runs_a_dry_run_and_never_purges(client, dispatched):
    client.post("/retire/preview", {"topic": TOPIC})

    assert dispatched == [{"topic": TOPIC, "dry_run": True, "requested_by": OPERATOR}]


def test_preview_logs_are_scoped_to_the_execution_it_started(client, monkeypatch):
    seen = {}

    def fake_logs(key, limit=80, execution=""):
        seen.update(key=key, execution=execution)
        return {"ok": True, "lines": [{"severity": "INFO", "text": "would delete …"}]}

    monkeypatch.setattr(jobs, "logs", fake_logs)
    monkeypatch.setattr(jobs, "recent", lambda key, limit=5: [{"name": "retire-abc12", "state": "running"}])

    body = client.get("/retire/preview/logs", {"topic": TOPIC, "execution": "retire-abc12"}).content.decode()

    assert seen == {"key": "retire", "execution": "retire-abc12"}
    assert "hx-trigger=\"every 5s\"" in body, "a running preview must keep polling"


def test_preview_stops_polling_once_the_run_ends(client, monkeypatch):
    monkeypatch.setattr(jobs, "logs", lambda key, limit=80, execution="": {"ok": True, "lines": []})
    monkeypatch.setattr(jobs, "recent",
                        lambda key, limit=5: [{"name": "retire-abc12", "state": "succeeded"}])

    body = client.get("/retire/preview/logs", {"topic": TOPIC, "execution": "retire-abc12"}).content.decode()

    assert "every 5s" not in body
    assert "succeeded" in body


def test_the_page_offers_the_serving_table_not_the_item_id(client, monkeypatch):
    """The item id (`hazards_qfaults`) is not addressable by the CLI — it retires a `_current`
    table — so the list has to carry the fully-qualified name the confirmation will be typed as."""
    from ops import stac

    monkeypatch.setattr(stac, "_get", lambda url: {"items": [
        {"id": "hazards_qfaults", "properties": {"ugs:dbt_schema": "hazards",
                                                 "ugs:layer": "hazards_qfaults_current",
                                                 "ugs:row_count": 42}},
        # An item published before the schema/layer properties existed: no table to address.
        {"id": "orphan_topic", "properties": {}},
    ]})
    stac._CACHE.clear()

    rows = stac.serving_topics()
    assert [r["fqn"] for r in rows] == [TOPIC, ""]

    body = client.get("/retire").content.decode()
    assert TOPIC in body
    assert "orphan_topic" not in body, "a topic with no serving table cannot be retired from here"


def test_the_console_is_iap_gated():
    assert Client().post("/retire/execute", {"topic": TOPIC, "confirm": TOPIC}).status_code == 403
