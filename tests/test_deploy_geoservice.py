"""scripts/deploy_geoservice.sh clears the api host's CDN cache only when what the image serves
changed. `gcloud` is a stub on PATH that logs its arguments."""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import shutil
import subprocess

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "deploy_geoservice.sh"

pytestmark = pytest.mark.skipif(not (shutil.which("bash") and shutil.which("sha256sum")),
                                reason="needs bash and coreutils")

GCLOUD = """#!/usr/bin/env bash
echo "$*" >> "$GCLOUD_LOG"
case "$1 $2 $3" in
  "run services describe") cat "$FAKE_DIR/service.json" ;;
  "run revisions describe") cat "$FAKE_DIR/revision.json" ;;
  "compute url-maps invalidate-cdn-cache") exit "$FAKE_CLEAR_RC" ;;
esac
"""


def _deploy(tmp_path, *, dockerfile: str, serving: str | None, clear_rc: int = 0,
            prod_tag_on_new: bool = False):
    (tmp_path / "bin").mkdir()
    stub = tmp_path / "bin" / "gcloud"
    stub.write_text(GCLOUD)
    stub.chmod(0o755)
    (tmp_path / "Dockerfile").write_text(dockerfile)
    traffic = [{"revisionName": "svc-old", "percent": 0}]
    if serving is not None:
        traffic.append({"revisionName": "svc-live", "percent": 100})
    if prod_tag_on_new:
        traffic.append({"revisionName": "svc-new", "tag": "prod", "percent": 0})
    (tmp_path / "service.json").write_text(json.dumps({"status": {"traffic": traffic}}))
    (tmp_path / "revision.json").write_text(json.dumps(
        {"spec": {"containers": [{"env": [{"name": "SERVED_BUILD", "value": serving or ""}]}]}}))
    log = tmp_path / "gcloud.log"
    env = {**os.environ, "PATH": f"{tmp_path / 'bin'}:{os.environ['PATH']}", "GCLOUD_LOG": str(log),
           "FAKE_DIR": str(tmp_path), "FAKE_CLEAR_RC": str(clear_rc), "REGION": "us-central1",
           "CDN_URL_MAP": "lb", "CDN_PROJECT": "maps-prod", "CDN_HOST": "api.example"}
    run = subprocess.run(["bash", str(SCRIPT), "svc", str(tmp_path / "Dockerfile"),
                          "FEATURESERVER_VERSION", "/*", "--image=img"],
                         env=env, capture_output=True, text=True, check=False)
    return run, log.read_text().splitlines() if log.exists() else []


DOCKERFILE = 'ARG FEATURESERVER_VERSION="v0.5.0"\nENV CACHE_TTL=300\n'


def _build(dockerfile: str) -> str:
    return "v0-5-0-" + hashlib.sha256(dockerfile.encode()).hexdigest()[:8]


def test_a_new_build_deploys_then_clears_the_cache(tmp_path):
    run, calls = _deploy(tmp_path, dockerfile=DOCKERFILE, serving="v0-4-0-00000000")
    assert run.returncode == 0, run.stderr
    assert f"run deploy svc --image=img --update-env-vars=SERVED_BUILD={_build(DOCKERFILE)} " \
           "--tag=prod" in calls
    assert calls[-1] == ("compute url-maps invalidate-cdn-cache lb --project=maps-prod "
                         "--host=api.example --path=/* --async")
    assert "run revisions describe svc-live --region=us-central1 --format=json" in calls


def test_the_same_build_keeps_the_cache(tmp_path):
    run, calls = _deploy(tmp_path, dockerfile=DOCKERFILE, serving=_build(DOCKERFILE))
    assert run.returncode == 0, run.stderr
    assert any(c.startswith("run deploy") for c in calls)
    assert not any("invalidate-cdn-cache" in c for c in calls)


def test_an_env_edit_at_the_same_release_clears_the_cache(tmp_path):
    edited = DOCKERFILE.replace("300", "600")
    run, calls = _deploy(tmp_path, dockerfile=edited, serving=_build(DOCKERFILE))
    assert run.returncode == 0, run.stderr
    assert "invalidate-cdn-cache" in calls[-1]


def test_no_prod_revision_clears_the_cache(tmp_path):
    run, calls = _deploy(tmp_path, dockerfile=DOCKERFILE, serving=None)
    assert run.returncode == 0, run.stderr
    assert "invalidate-cdn-cache" in calls[-1]


def test_a_failed_clear_fails_the_step_and_prints_the_command(tmp_path):
    run, _ = _deploy(tmp_path, dockerfile=DOCKERFILE, serving=None, clear_rc=1)
    assert run.returncode == 1
    assert "gcloud compute url-maps invalidate-cdn-cache lb" in run.stderr
    assert "--path=/\\*" in run.stderr  # quoted, so pasting it does not glob


def test_a_dockerfile_without_the_release_deploys_nothing(tmp_path):
    run, calls = _deploy(tmp_path, dockerfile="FROM scratch\n", serving=None)
    assert run.returncode == 1
    assert not any(c.startswith("run deploy") for c in calls)


def test_a_rerun_after_a_failed_traffic_move_still_clears(tmp_path):
    """The prod tag already names the new build's revision, but the old one still takes the traffic."""
    run, calls = _deploy(tmp_path, dockerfile=DOCKERFILE, serving="v0-4-0-00000000",
                         prod_tag_on_new=True)
    assert run.returncode == 0, run.stderr
    assert "run revisions describe svc-live --region=us-central1 --format=json" in calls
    assert "invalidate-cdn-cache" in calls[-1]
