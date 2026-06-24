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
    tasks: int | None = None  # override task_count (parallel shards) at run time; None = job default


# The triggerable warehouse jobs (Cloud Run job names from cloudbuild.yaml).
JOBS: dict[str, Job] = {j.key: j for j in [
    Job("harvest", "geolmap-harvest", "Harvest COGs",
        "Convert publication map plates → COGs (SKIP_EXISTING; safe to re-run). Heavy. "
        "Runs as 5 parallel shards (each task strides 1/5 of the worklist).", danger=True, tasks=5),
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
    """Execute a job. Returns {ok, execution|message}. Honors JOBS_DRY_RUN.

    For jobs with a `tasks` override (e.g. harvest=5), passes a RunJobRequest.Overrides(task_count=N)
    so Cloud Run spawns N parallel shards — the job's CLI self-shards via CLOUD_RUN_TASK_INDEX/COUNT.
    """
    job = JOBS.get(key)
    if not job:
        return {"ok": False, "message": f"unknown job {key!r}"}
    shards = f" ×{job.tasks} shards" if job.tasks else ""
    if settings.JOBS_DRY_RUN:
        return {"ok": True, "message": f"DRY-RUN: would execute {job.name}{shards}", "dry_run": True}
    try:
        from google.cloud import run_v2
        client = run_v2.JobsClient()
        req = run_v2.RunJobRequest(name=_job_path(job))
        if job.tasks:
            req.overrides = run_v2.RunJobRequest.Overrides(task_count=job.tasks)
        op = client.run_job(request=req)
        exec_name = (op.metadata.name if op.metadata else "") or "(started)"
        return {"ok": True, "message": f"started {job.name}{shards}", "execution": exec_name}
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


def logs(key: str, limit: int = 80) -> dict:
    """Recent Cloud Logging lines for a job (near-live; newest fetched, returned chronological).

    Needs `roles/logging.viewer` on the runtime SA. Returns {ok, lines, message?} so the view can
    show *why* it's empty instead of a silent blank — the whole point is process visibility.
    """
    job = JOBS.get(key)
    if not job:
        return {"ok": False, "lines": [], "message": f"unknown job {key!r}"}
    if settings.JOBS_DRY_RUN:
        return {"ok": True, "dry_run": True, "lines": [],
                "message": "DRY-RUN — live logs show here once a real run is triggered."}
    try:
        import google.cloud.logging as gcloud_logging
        client = gcloud_logging.Client(project=settings.GCP_PROJECT)
        flt = f'resource.type="cloud_run_job" resource.labels.job_name="{job.name}"'
        lines = []
        for e in client.list_entries(filter_=flt, order_by="timestamp desc",
                                     page_size=limit, max_results=limit):
            p = e.payload
            text = p if isinstance(p, str) else (p.get("message") or p.get("msg") or str(p)) \
                if isinstance(p, dict) else str(p)
            labels = e.labels or {}
            lines.append({
                "time": e.timestamp.isoformat() if e.timestamp else "",
                "severity": (e.severity or "DEFAULT"),
                "task": labels.get("run.googleapis.com/task_index", ""),
                "category": (p.get("category", "") if isinstance(p, dict) else ""),
                "text": (text or "").rstrip(),
            })
        lines.reverse()  # oldest → newest, like a tail
        return {"ok": True, "lines": lines}
    except Exception as e:  # noqa: BLE001 — surface to the operator
        return {"ok": False, "lines": [], "message": f"{type(e).__name__}: {e}"}


def console_logs_url(key: str) -> str:
    """Deep-link to this job's logs in the Cloud Console — the authoritative, always-complete view
    to open whenever a job is kicked off (the in-app tail is a convenience, not a replacement)."""
    job = JOBS.get(key)
    if not job:
        return ""
    return (f"https://console.cloud.google.com/run/jobs/details/"
            f"{settings.GCP_REGION}/{job.name}/logs?project={settings.GCP_PROJECT}")


HARVEST_JOB = "geolmap-harvest"  # the only job that emits per-pub structured logs


def pub_logs(series_id: str, limit: int = 200) -> dict:
    """Harvest log lines for ONE publication, chronological. Reads the structured `jsonPayload.
    series_id` the harvester now emits — the per-pub report. {ok, lines, message?}."""
    if settings.JOBS_DRY_RUN:
        return {"ok": True, "dry_run": True, "lines": [],
                "message": "DRY-RUN — per-pub logs appear here after a real harvest run."}
    try:
        import google.cloud.logging as gcloud_logging
        client = gcloud_logging.Client(project=settings.GCP_PROJECT)
        sid = (series_id or "").replace('"', "").replace("\\", "")  # guard the filter literal
        flt = (f'resource.type="cloud_run_job" resource.labels.job_name="{HARVEST_JOB}" '
               f'jsonPayload.series_id="{sid}"')
        lines = []
        for e in client.list_entries(filter_=flt, order_by="timestamp desc",
                                     page_size=limit, max_results=limit):
            p = e.payload if isinstance(e.payload, dict) else {}
            lines.append({
                "time": e.timestamp.isoformat() if e.timestamp else "",
                "severity": (e.severity or p.get("severity") or "INFO"),
                "step": p.get("step", ""),
                "category": p.get("category", ""),
                "text": (p.get("message") if isinstance(e.payload, dict) else str(e.payload)) or "",
            })
        lines.reverse()
        return {"ok": True, "lines": lines}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "lines": [], "message": f"{type(e).__name__}: {e}"}
