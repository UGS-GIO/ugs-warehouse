"""The Pub/Sub push handler starts the ingest job instead of ingesting in-request.

Ingesting inline put a DuckDB/geometry workload in a 512Mi HTTP container: it needed a 3600s
request timeout, an OOM killed the container mid-request, and the resulting 429s were retried by
Pub/Sub into a redelivery storm. These pin that the handler is now a router — it must ack fast and
must never call ingest_topic unless explicitly asked to.
"""
from __future__ import annotations

import base64
import json

import pytest
from fastapi.testclient import TestClient

from service import main as svc


def _envelope(schema: str, topic: str) -> dict:
    payload = json.dumps({"schema": schema, "topic": topic}).encode()
    return {"message": {"data": base64.b64encode(payload).decode()}}


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(svc, "INGEST_INLINE", False)
    monkeypatch.setattr(svc, "INGEST_JOB_PROJECT", "a-project")
    return TestClient(svc.app)


def test_push_starts_the_job_and_does_not_ingest(client, monkeypatch):
    started = []
    monkeypatch.setattr(svc, "_start_ingest_job", lambda fqn: started.append(fqn) or "exec-1")
    monkeypatch.setattr(svc, "ingest_topic", lambda *a, **k: pytest.fail("must not ingest in-request"))

    r = client.post("/", json=_envelope("hazards", "hazards_qfaults_current"))

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "queued"
    assert body["execution"] == "exec-1"
    assert started == ["hazards.hazards_qfaults_current"]


def test_a_failure_to_start_still_acks(client, monkeypatch):
    """A 5xx is what Pub/Sub retries into a storm. Report the error, but ack."""
    def boom(_fqn):
        raise RuntimeError("permission denied")
    monkeypatch.setattr(svc, "_start_ingest_job", boom)

    r = client.post("/", json=_envelope("hazards", "hazards_qfaults_current"))

    assert r.status_code == 200
    assert r.json()["status"] == "error"


def test_unsupported_schema_never_starts_a_job(client, monkeypatch):
    monkeypatch.setattr(svc, "_start_ingest_job", lambda fqn: pytest.fail("should not start a job"))
    r = client.post("/", json=_envelope("gwportal", "gwportal_wells_current"))
    assert r.status_code == 200
    assert r.json()["status"] == "skipped"


def test_malformed_payload_never_starts_a_job(client, monkeypatch):
    monkeypatch.setattr(svc, "_start_ingest_job", lambda fqn: pytest.fail("should not start a job"))
    r = client.post("/", json={"message": {"data": base64.b64encode(b'{"nope":1}').decode()}})
    assert r.status_code == 200
    assert r.json()["status"] == "skipped"


def test_inline_mode_still_ingests_in_process(monkeypatch):
    """The local-dev escape hatch has to keep working."""
    monkeypatch.setattr(svc, "INGEST_INLINE", True)
    monkeypatch.setattr(svc, "_start_ingest_job", lambda fqn: pytest.fail("inline must not queue"))
    monkeypatch.setattr(svc, "ingest_topic", lambda *a, **k: 0)

    r = TestClient(svc.app).post("/", json=_envelope("hazards", "hazards_qfaults_current"))

    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_missing_project_is_reported_not_raised(client, monkeypatch):
    monkeypatch.setattr(svc, "INGEST_JOB_PROJECT", "")
    r = client.post("/", json=_envelope("hazards", "hazards_qfaults_current"))
    assert r.status_code == 200
    assert r.json()["status"] == "error"
