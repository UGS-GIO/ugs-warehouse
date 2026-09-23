"""Publishing a layer starts the topic-thumbnail job, so its preview arrives minutes later instead of at
the nightly run. Only the prod ingest and restyle jobs set TOPIC_THUMBS_JOB; without it (local runs,
the review deployment) nothing is started.
"""
from __future__ import annotations

import pathlib
import sys
from types import SimpleNamespace

import pytest
import yaml
from google.cloud import run_v2

from ugs_warehouse.core import jobs, stac
from ugs_warehouse.vector import ingest
from ugs_warehouse.vector.topics import Topic

JOB = "projects/p/locations/us-central1/jobs/ugs-topics-thumbs"
CLOUDBUILD = pathlib.Path(__file__).resolve().parents[1] / "cloudbuild.yaml"


@pytest.fixture
def calls(monkeypatch) -> list[SimpleNamespace]:
    """Each run_job call to the Cloud Run Admin API as (request, timeout); its client is the only
    thing faked."""
    sent: list[SimpleNamespace] = []

    class JobsClient:
        def run_job(self, request, timeout=None):
            sent.append(SimpleNamespace(request=request, timeout=timeout))
            return SimpleNamespace(metadata=SimpleNamespace(name=f"{JOB}/executions/x1"))

    monkeypatch.setattr(run_v2, "JobsClient", JobsClient)
    monkeypatch.setenv("TOPIC_THUMBS_JOB", JOB)
    return sent


def test_one_topic_runs_as_a_single_task_for_just_that_topic(calls):
    jobs.start_topic_thumbs(["wetlands_riverine"])
    (call,) = calls
    assert call.request.name == JOB
    (override,) = call.request.overrides.container_overrides
    assert list(override.args) == ["-m", "ugs_warehouse.vector.thumbs", "wetlands_riverine"]
    assert call.request.overrides.task_count == 1


def test_all_topics_runs_the_job_as_deployed(calls):
    jobs.start_topic_thumbs()
    (call,) = calls
    assert call.request.name == JOB
    assert not call.request.overrides.container_overrides


def test_the_start_is_time_bounded_so_a_stalled_api_cannot_hold_the_ingest(calls):
    """The client's default is no timeout; a hang would keep the ingest task alive until its own
    timeout and then fail it, after the layer was published."""
    jobs.start_topic_thumbs(["wetlands_riverine"])
    (call,) = calls
    assert call.timeout is not None and 0 < call.timeout <= 120


def test_an_empty_topic_list_starts_nothing(calls):
    """Only None means every topic; an empty list must not quietly become a full 3-task run."""
    jobs.start_topic_thumbs([])
    assert calls == []


def test_the_topic_override_matches_how_the_job_is_deployed(calls):
    """The override replaces the job's args but keeps its command, so it only works while the deploy
    runs `python` with `-m <module> ...`. Pins the two together across cloudbuild.yaml and jobs.py."""
    steps = yaml.safe_load(CLOUDBUILD.read_text())["steps"]
    deploy = next(s for s in steps if s.get("id") == "deploy-topics-thumbs-job")
    flags = dict(a.split("=", 1) for a in deploy["args"] if a.startswith("--") and "=" in a)
    assert flags["--command"] == "python"
    jobs.start_topic_thumbs(["wetlands_riverine"])
    sent = list(calls[0].request.overrides.container_overrides[0].args)
    assert sent[:2] == flags["--args"].split(",")[:2]


def test_nothing_starts_without_the_job_configured(calls, monkeypatch):
    monkeypatch.delenv("TOPIC_THUMBS_JOB")
    jobs.start_topic_thumbs(["wetlands_riverine"])
    assert calls == []


def test_a_start_that_fails_is_logged_and_never_raised(monkeypatch, capsys):
    """The layer is already published; failing its ingest would fire the job alert over a preview,
    and the nightly run catches it up."""
    class Denied:
        def run_job(self, request, timeout=None):
            raise PermissionError("403 run.jobs.runWithOverrides denied")

    monkeypatch.setattr(run_v2, "JobsClient", Denied)
    monkeypatch.setenv("TOPIC_THUMBS_JOB", JOB)
    jobs.start_topic_thumbs(["wetlands_riverine"])
    assert JOB in capsys.readouterr().err


def _ingest(monkeypatch, *argv: str) -> int:
    monkeypatch.setattr(sys, "argv", ["ugs_warehouse.vector.ingest", *argv])
    return ingest.main()


@pytest.fixture
def kicked(monkeypatch) -> list:
    calls: list = []
    monkeypatch.setattr(jobs, "start_topic_thumbs", lambda item_ids=None: calls.append(item_ids))
    return calls


@pytest.mark.parametrize("rc", [0, 1])
def test_ingesting_a_layer_starts_its_thumbnail(monkeypatch, kicked, rc):
    """Even a run with a failed sink can have republished the layer; the thumbnail job works out
    whether anything it draws changed. The ingest's own exit code is unchanged."""
    monkeypatch.setattr(ingest, "ingest_topic", lambda topic, **kw: rc)
    assert _ingest(monkeypatch, "--topic", "wetlands.wetlands_riverine_current") == rc
    assert kicked == [["wetlands_riverine"]]


def test_a_dry_run_ingest_starts_nothing(monkeypatch, kicked):
    monkeypatch.setattr(ingest, "ingest_topic", lambda topic, **kw: 0)
    assert _ingest(monkeypatch, "--topic", "wetlands.wetlands_riverine_current", "--dry-run") == 0
    assert kicked == []


def test_an_all_topics_ingest_starts_one_thumbnail_run_after_its_refresh(monkeypatch, kicked):
    order: list = []
    topics = [Topic.parse("wetlands.wetlands_riverine_current"), Topic.parse("hazards.hazards_qfaults_current")]
    monkeypatch.setattr(ingest, "_backend", lambda: SimpleNamespace(discover=lambda: topics))
    monkeypatch.setattr(ingest, "ingest_topic", lambda topic, **kw: 0)
    monkeypatch.setattr(stac, "refresh_catalog", lambda: order.append("refresh"))
    monkeypatch.setattr(jobs, "start_topic_thumbs", lambda item_ids=None: order.append(("thumbs", item_ids)))
    assert _ingest(monkeypatch, "--all") == 0
    assert order == ["refresh", ("thumbs", None)]
