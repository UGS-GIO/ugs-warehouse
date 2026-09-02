"""Pub/Sub push handler ack semantics.

The subscription has no dead-letter policy and a 1-day retention (`scripts/provision.sh`), so a
non-2xx on a message that can never succeed buys 24h of redelivery. Anything the Topic rules
reject is malformed, not transient — it has to ack.
"""
import base64
import json
import sys
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

# `service/` isn't in the editable install and pythonpath carries only `src`, so without this it
# imports under `python -m pytest` (which adds CWD) but not CI's bare `pytest`.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from service.main import app  # noqa: E402 — needs the sys.path insert above

client = TestClient(app)


def _push(payload: dict):
    body = base64.b64encode(json.dumps(payload).encode()).decode()
    return client.post("/", json={"message": {"data": body}, "subscription": "s"})


def test_malformed_layer_acks_instead_of_retrying():
    r = _push({"schema": "hazards", "topic": 'x" UNION SELECT 1 --'})
    assert r.status_code == 200
    assert r.json()["status"] == "skipped"


def test_non_string_layer_acks():
    """A TypeError here would escape as a 500 and put Pub/Sub into redelivery."""
    r = _push({"schema": "emp", "topic": 123})
    assert r.status_code == 200
    assert r.json()["status"] == "skipped"


def test_unsupported_schema_still_acks():
    r = _push({"schema": "gwportal", "topic": "gw_wells_current"})
    assert r.status_code == 200
    assert r.json()["status"] == "skipped"


def test_valid_payload_is_acted_on():
    """The handler now starts the ingest job rather than ingesting in-request (see
    tests/test_service_push_starts_job.py); a valid payload must still be acted on, not skipped."""
    with patch("service.main._start_ingest_job", return_value="exec-1") as start, \
         patch("service.main.INGEST_JOB_PROJECT", "a-project"), \
         patch("service.main.INGEST_INLINE", False):
        r = _push({"schema": "hazards", "topic": "hazards_qfaults_current"})
    assert r.status_code == 200
    assert r.json()["status"] == "queued"
    start.assert_called_once()
