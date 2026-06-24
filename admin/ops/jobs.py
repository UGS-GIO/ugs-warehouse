"""Trigger + monitor the warehouse's Cloud Run jobs (google-cloud-run v2).

The console never runs business logic — it only *executes* the existing jobs (the same ones the
work box runs by hand) and reports their execution status. `JOBS_DRY_RUN` (default in DEBUG) skips
the real API call so the UI is exercisable without GCP creds.

Auth: Application Default Credentials. In prod the Cloud Run service account needs `run.developer`
(run.jobs.run + run.executions.list) on these jobs.
"""
from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings


@dataclass(frozen=True)
class Job:
    key: str
    name: str          # Cloud Run job resource name
    label: str
    description: str
    danger: bool = False  # costs money / heavy → extra confirm in the UI


# The triggerable warehouse jobs (Cloud Run job names from cloudbuild.yaml).
JOBS: dict[str, Job] = {j.key: j for j in [
    Job("harvest", "geolmap-harvest", "Harvest COGs",
        "Convert publication map plates → COGs (SKIP_EXISTING; safe to re-run). Heavy.", danger=True),
    Job("pubs-ingest", "ugs-pubs-ingest", "Rebuild pubs STAC",
        "Re-read pub metadata + attach harvested COGs/thumbnails to the STAC items."),
    Job("ingest", "ugs-warehouse-ingest", "Vector reingest (--all)",
        "Full vector reingest — gengis, feature_id, classification/table, proj:code, FK relationships.",
        danger=True),
    Job("restyle", "ugs-warehouse-restyle", "Rebind styles",
        "Re-fetch the ugs-styles manifest + rebind renders onto the STAC items (no reingest)."),
]}


def _job_path(job: Job) -> str:
    return f"projects/{settings.GCP_PROJECT}/locations/{settings.GCP_REGION}/jobs/{job.name}"


def run(key: str) -> dict:
    """Execute a job. Returns {ok, execution|message}. Honors JOBS_DRY_RUN."""
    job = JOBS.get(key)
    if not job:
        return {"ok": False, "message": f"unknown job {key!r}"}
    if settings.JOBS_DRY_RUN:
        return {"ok": True, "message": f"DRY-RUN: would execute {job.name}", "dry_run": True}
    try:
        from google.cloud import run_v2
        client = run_v2.JobsClient()
        op = client.run_job(name=_job_path(job))
        exec_name = (op.metadata.name if op.metadata else "") or "(started)"
        return {"ok": True, "message": f"started {job.name}", "execution": exec_name}
    except Exception as e:  # noqa: BLE001 — surface the error to the operator, don't crash the view
        return {"ok": False, "message": f"{type(e).__name__}: {e}"}


def recent(key: str, limit: int = 5) -> list[dict]:
    """Recent executions of a job, newest first, with status. [] on dry-run / error."""
    job = JOBS.get(key)
    if not job or settings.JOBS_DRY_RUN:
        return []
    try:
        from google.cloud import run_v2
        client = run_v2.ExecutionsClient()
        out = []
        for ex in client.list_executions(parent=_job_path(job)):
            running = ex.running_count or 0
            failed = ex.failed_count or 0
            succeeded = ex.succeeded_count or 0
            state = ("running" if running else "failed" if failed else "succeeded"
                     if succeeded else "pending")
            out.append({
                "name": ex.name.split("/")[-1],
                "state": state,
                "succeeded": succeeded, "failed": failed, "running": running,
                "created": ex.create_time.isoformat() if ex.create_time else "",
            })
            if len(out) >= limit:
                break
        return out
    except Exception:
        return []
