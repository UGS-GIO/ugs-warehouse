import json
import logging
import sys

from ugs_warehouse.core import logs


def _record(msg, **extra):
    rec = logging.LogRecord("ugs-warehouse.ingest", logging.ERROR, __file__, 1, msg, (), None)
    rec.__dict__.update(extra)
    return rec


def test_json_line_carries_severity_message_and_fields():
    doc = json.loads(logs.JsonFormatter().format(_record("sink pmtiles FAILED", topic="a.b_current")))
    assert doc == {"severity": "ERROR", "message": "sink pmtiles FAILED",
                   "logger": "ugs-warehouse.ingest", "topic": "a.b_current"}


def test_traceback_goes_into_the_message():
    try:
        raise ValueError("boom")
    except ValueError:
        rec = logging.LogRecord("x", logging.ERROR, __file__, 1, "failed", (), sys.exc_info())
    doc = json.loads(logs.JsonFormatter().format(rec))
    assert doc["message"].startswith("failed\nTraceback") and "ValueError: boom" in doc["message"]


def test_stack_info_goes_into_the_message():
    rec = _record("failed")
    rec.stack_info = "Stack (most recent call last):\n  here"
    assert json.loads(logs.JsonFormatter().format(rec))["message"] == f"failed\n{rec.stack_info}"


def test_extra_fields_cannot_replace_the_standard_keys():
    doc = json.loads(logs.JsonFormatter().format(_record("real", severity="DEBUG", _private=1)))
    assert doc["severity"] == "ERROR" and doc["message"] == "real" and "_private" not in doc


def test_json_only_on_cloud_run(monkeypatch):
    root = logging.getLogger()
    monkeypatch.setattr(root, "handlers", list(root.handlers))  # restored after the test
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.delenv("CLOUD_RUN_JOB", raising=False)
    logs.setup()
    assert not isinstance(logging.getLogger().handlers[0].formatter, logs.JsonFormatter)
    monkeypatch.setenv("CLOUD_RUN_JOB", "ugs-warehouse-ingest")
    logs.setup()
    assert isinstance(logging.getLogger().handlers[0].formatter, logs.JsonFormatter)
