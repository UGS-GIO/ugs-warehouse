"""Trigger + monitor the warehouse's Cloud Run jobs (google-cloud-run v2).

The console never runs business logic — it only *executes* the existing jobs (the same ones
run by hand) and reports their execution status. `JOBS_DRY_RUN` (default in DEBUG) skips
the real API call so the UI is exercisable without GCP creds.

Auth: Application Default Credentials. In prod the Cloud Run service account needs `run.developer`
(run.jobs.run + run.executions.list) on these jobs.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from django.conf import settings


def series_code(sid: str) -> str:
    """Viewer collection id for a pub = its alpha series prefix (OFR-593 → OFR). Matches the
    warehouse's sink_stac.series_code so deep-links resolve to the right nested collection."""
    m = re.match(r"[A-Za-z]+", (sid or "").strip())
    return m.group(0).upper() if m else "OTHER"


@dataclass(frozen=True)
class Job:
    key: str
    name: str          # Cloud Run job resource name
    label: str
    description: str
    danger: bool = False  # costs money / heavy → extra confirm in the UI
    tasks: int | None = None  # override task_count (parallel shards) at run time; None = job default
    tiers: tuple[tuple[str, str], ...] | None = None  # per-variant regen (value, label); e.g. mosaic scale tiers
    force_toggle: bool = False  # render a "force rebuild" checkbox (ingest: override the default skip-unchanged)


# Pipeline stages mirror the Architecture page (docs/ARCHITECTURE.md + viewer Architecture.tsx) so
# the ops console and the architecture diagram tell the same story. Upstream ①/② (dataELT + Pub/Sub
# #418) are automatic — not operator-triggered — so the console starts at ③, the first stage an
# operator drives, and ends at ⑥ (serving) as read-only status.
STAGES = [
    {"n": "③", "title": "Warehouse transform", "jobs": ["ingest"],
     "blurb": "Reproject → EPSG:4326 · h3_r9 · hilbert, then fan out to DuckLake · GeoParquet · "
              "PMTiles · STAC. One DuckDB streaming pass."},
    {"n": "④", "title": "Styling", "jobs": ["restyle", "topics-thumbs"],
     "blurb": "Rebind ugs-styles renders onto STAC items by id — seconds, no reingest, no tiles rebuilt. "
              "Then render each topic's styled PMTiles → preview thumbnail (content-hash skip; "
              "re-renders only changed styles)."},
    {"n": "⑤", "title": "Publications", "jobs": ["pubs-pipeline", "harvest", "thumbs", "pubs-ingest",
                                                 "graph", "fts", "embed"],
     "blurb": "Scanned geologic maps → COGs (GDAL); cover thumbnails (PDF page 1) for every pub → "
              "STAC (3 collections). One-click Full refresh runs thumbnails → rebuild for you, or "
              "step through harvest / thumbnail / rebuild individually. Search corpora (full-text + "
              "semantic) rebuild from the same pub set."},
    {"n": "⑥", "title": "Geologic-map rasters", "jobs": ["mosaics"],
     "blurb": "Per-scale raster PMTiles mosaics of the published geologic maps (GDAL warp → pmtiles). "
              "Rebuild all tiers at once, or regenerate a single scale tier on its own."},
]


# The triggerable warehouse jobs (Cloud Run job names from cloudbuild.yaml).
JOBS: dict[str, Job] = {j.key: j for j in [
    Job("pubs-pipeline", "ugs-pubs-pipeline", "Full refresh (one click)",
        "Orchestrator: loops Cover thumbnails to completion, then Rebuild pubs STAC — binds covers / "
        "in-this-issue / volume + builds the search corpus. Runs server-side for as long as it takes "
        "(survives closing this tab). Use this instead of the 3-step dance.", danger=True),
    Job("harvest", "geolmap-harvest", "Harvest COGs",
        "Convert publication map plates → COGs (SKIP_EXISTING; safe to re-run). Heavy. "
        "Runs as 5 parallel shards (each task strides 1/5 of the worklist).", danger=True, tasks=5),
    Job("pubs-ingest", "ugs-pubs-ingest", "Rebuild pubs STAC",
        "Re-read pub metadata + attach harvested COGs/thumbnails to the STAC items."),
    Job("thumbs", "ugs-pubs-thumbs", "Cover thumbnails",
        "Render each pub's PDF first page → cover PNG (every pub incl. Survey Notes; SKIP_EXISTING; "
        "5 shards). Then Rebuild pubs STAC to bind the previews.", tasks=5),
    Job("ingest", "ugs-warehouse-ingest", "Vector reingest (--all)",
        "Vector reingest — gengis, feature_id, classification/table, proj:code, FK relationships. "
        "Skips topics whose content + tiling is unchanged; tick Force to rebuild every topic.",
        danger=True, force_toggle=True),
    Job("restyle", "ugs-warehouse-restyle", "Rebind styles",
        "Re-fetch the ugs-styles manifest + rebind renders onto the STAC items (no reingest)."),
    Job("fts", "ugs-pubs-fts", "Build full-text search",
        "Rebuild the all-pub full-text-search DuckDB (BM25 FTS) → CDN. Run after pub text changes."),
    Job("embed", "ugs-pubs-embed", "Build semantic search",
        "Chunk + embed every pub (bge-small) → DuckDB VSS (HNSW) → CDN. Heavy. Run after pub set or "
        "classification changes.", danger=True),
    Job("mosaics", "ugs-geolmap-mosaics", "Raster mosaics (all tiers)",
        "Rebuild the per-scale raster PMTiles mosaics of the published geologic maps. Heavy "
        "(GDAL warp + tile). Use the per-tier buttons to regenerate just one scale.",
        danger=True, tiers=(("24k", "1:24,000"), ("250k", "1:250,000"), ("500k", "1:500,000"))),
    Job("topics-thumbs", "ugs-topics-thumbs", "Topic thumbnails",
        "Render each vector serving-topic's styled PMTiles → preview PNG (headless MapLibre; a neutral "
        "sand style when unstyled). Content-hash skip — re-renders only topics whose style changed. "
        "Run a Vector reingest after to bind the new thumbnail assets. 3 shards.", tasks=3),
    Job("graph", "ugs-pubs-graph", "Build knowledge graph",
        "Rebuild the publications knowledge graph (nodes/edges Parquet) — citation + co-author + "
        "semantic edges. Reads pub metadata + embeddings; safe to re-run."),
]}


def _job_path(job: Job) -> str:
    return f"projects/{settings.GCP_PROJECT}/locations/{settings.GCP_REGION}/jobs/{job.name}"


def _logging_client():
    """Cloud Logging client (lazy import — keeps the module importable without GCP libs/creds)."""
    import google.cloud.logging as gcloud_logging
    return gcloud_logging.Client(project=settings.GCP_PROJECT)


def _fmt_dur(start, end) -> str:
    """Run duration 'Nm SSs' / 'Ns' when both ends are known, else ''."""
    if not (start and end):
        return ""
    secs = int((end - start).total_seconds())
    if secs < 0:
        return ""
    return f"{secs // 60}m{secs % 60:02d}s" if secs >= 60 else f"{secs}s"


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


# The ingest job is deployed with command=cloudrun_entrypoint.sh, args=[--all]; the entrypoint
# execs `python -m ugs_warehouse.vector.ingest "$@"`. Overriding args to add --force turns OFF the
# default skip-unchanged (rebuilds every topic). Args REPLACE the configured ones, so --all stays.
INGEST_FORCE_ARGS = ["--all", "--force"]


def run_ingest(force: bool = False) -> dict:
    """Execute the vector reingest. Default runs it as configured (--all, which now skips unchanged
    topics). force=True overrides args to add --force so every topic rebuilds regardless."""
    if not force:
        return run("ingest")
    job = JOBS["ingest"]
    if settings.JOBS_DRY_RUN:
        return {"ok": True, "message": f"DRY-RUN: would execute {job.name} --all --force",
                "dry_run": True}
    try:
        from google.cloud import run_v2
        client = run_v2.JobsClient()
        override = run_v2.RunJobRequest.Overrides.ContainerOverride(args=INGEST_FORCE_ARGS)
        req = run_v2.RunJobRequest(
            name=_job_path(job),
            overrides=run_v2.RunJobRequest.Overrides(container_overrides=[override]))
        op = client.run_job(request=req)
        exec_name = (op.metadata.name if op.metadata else "") or "(started)"
        return {"ok": True, "message": f"started {job.name} --all --force", "execution": exec_name}
    except Exception as e:  # noqa: BLE001 — surface the error to the operator
        return {"ok": False, "message": f"{type(e).__name__}: {e}"}


MOSAIC_MODULE = "ugs_warehouse.pubs.geolmap_mosaics"


def rebuild_mosaic(tier: str) -> dict:
    """Regenerate ONE scale tier's raster mosaic (e.g. just 24k) without rebuilding the others.
    Overrides the mosaics job's args for this execution only — base command stays `python`."""
    job = JOBS.get("mosaics")
    valid = {t for t, _ in (job.tiers or ())}
    if tier not in valid:
        return {"ok": False, "message": f"unknown mosaic tier {tier!r}"}
    if settings.JOBS_DRY_RUN:
        return {"ok": True, "message": f"DRY-RUN: would rebuild the {tier} mosaic", "dry_run": True}
    try:
        from google.cloud import run_v2
        client = run_v2.JobsClient()
        override = run_v2.RunJobRequest.Overrides.ContainerOverride(
            args=["-m", MOSAIC_MODULE, "--scale", tier])
        req = run_v2.RunJobRequest(
            name=_job_path(job),
            overrides=run_v2.RunJobRequest.Overrides(container_overrides=[override], task_count=1))
        client.run_job(request=req)
        return {"ok": True, "message": f"rebuilding the {tier} mosaic"}
    except Exception as e:  # noqa: BLE001 — surface the error to the operator
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
            start, done = ex.create_time, ex.completion_time
            out.append({
                "name": ex.name.split("/")[-1],
                "full_name": ex.name,
                "state": state,
                "cancelable": bool(running),
                "succeeded": succeeded, "failed": failed, "running": running,
                "duration": _fmt_dur(start, done),
                "created": start.isoformat() if start else "",  # UTC ISO — formatted client-side
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
        client = _logging_client()
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
                "execution": labels.get("run.googleapis.com/execution_name", "").split("/")[-1],
                "category": (p.get("category", "") if isinstance(p, dict) else ""),
                "text": (text or "").rstrip(),
            })
        lines.reverse()  # oldest → newest, like a tail
        # latest_execution = the newest run → the template draws a "run started here" divider at it.
        return {"ok": True, "lines": lines,
                "latest_execution": lines[-1]["execution"] if lines else ""}
    except Exception as e:  # noqa: BLE001 — surface to the operator
        return {"ok": False, "lines": [], "message": f"{type(e).__name__}: {e}"}


def all_executions(limit_per_job: int = 8) -> list[dict]:
    """Every job's recent executions, merged + newest first — the executions feed. Each row carries
    its job label/key so one table spans all jobs. [] in dry-run."""
    out: list[dict] = []
    for key, job in JOBS.items():
        for ex in recent(key, limit=limit_per_job):
            out.append({**ex, "key": key, "job": job.name, "label": job.label})
    out.sort(key=lambda e: e.get("created") or "", reverse=True)
    return out


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
        client = _logging_client()
        sid = (series_id or "").replace('"', "").replace("\\", "")  # guard the filter literal
        flt = (f'resource.type="cloud_run_job" resource.labels.job_name="{HARVEST_JOB}" '
               f'jsonPayload.series_id="{sid}"')
        lines = []
        for e in client.list_entries(filter_=flt, order_by="timestamp desc",
                                     page_size=limit, max_results=limit):
            p = e.payload if isinstance(e.payload, dict) else {}
            labels = e.labels or {}
            lines.append({
                "time": e.timestamp.isoformat() if e.timestamp else "",
                "severity": (e.severity or p.get("severity") or "INFO"),
                "step": p.get("step", ""),
                "execution": labels.get("run.googleapis.com/execution_name", "").split("/")[-1],
                "category": p.get("category", ""),
                "text": (p.get("message") if isinstance(e.payload, dict) else str(e.payload)) or "",
            })
        lines.reverse()
        return {"ok": True, "lines": lines,
                "latest_execution": lines[-1]["execution"] if lines else ""}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "lines": [], "message": f"{type(e).__name__}: {e}"}


def cancel(execution_full_name: str) -> dict:
    """Cancel a running execution (so you don't have to open the Cloud Run console)."""
    if settings.JOBS_DRY_RUN:
        return {"ok": True, "message": "DRY-RUN: would cancel the execution", "dry_run": True}
    try:
        from google.cloud import run_v2
        run_v2.ExecutionsClient().cancel_execution(name=execution_full_name)
        return {"ok": True, "message": "cancel requested"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"{type(e).__name__}: {e}"}


def reharvest_one(series_id: str) -> dict:
    """Re-harvest a SINGLE publication with --force (fix one bad COG without an --all run). Overrides
    the harvest job's args for this execution only — base command stays `python`."""
    job = JOBS.get("harvest")
    sid = (series_id or "").strip()
    if not sid:
        return {"ok": False, "message": "no series id"}
    if settings.JOBS_DRY_RUN:
        return {"ok": True, "message": f"DRY-RUN: would re-harvest {sid} --force", "dry_run": True}
    try:
        from google.cloud import run_v2
        client = run_v2.JobsClient()
        override = run_v2.RunJobRequest.Overrides.ContainerOverride(
            args=["-m", "ugs_warehouse.pubs.harvest", sid, "--force"])
        req = run_v2.RunJobRequest(
            name=_job_path(job),
            overrides=run_v2.RunJobRequest.Overrides(container_overrides=[override], task_count=1))
        client.run_job(request=req)
        return {"ok": True, "message": f"re-harvesting {sid} (--force)"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"{type(e).__name__}: {e}"}


def reharvest_many(series_ids: list[str]) -> dict:
    """Re-harvest a SPECIFIC set of pubs (e.g. everything in the attention list) in one job run —
    passes the ids as args instead of --all, so only these are processed. Sharded for parallelism."""
    sids = [s.strip() for s in series_ids if s and s.strip()]
    if not sids:
        return {"ok": False, "message": "no pubs to re-harvest"}
    if settings.JOBS_DRY_RUN:
        return {"ok": True, "dry_run": True,
                "message": f"DRY-RUN: would re-harvest {len(sids)} pub(s) --force"}
    try:
        from google.cloud import run_v2
        client = run_v2.JobsClient()
        override = run_v2.RunJobRequest.Overrides.ContainerOverride(
            args=["-m", "ugs_warehouse.pubs.harvest", *sids, "--force"])
        req = run_v2.RunJobRequest(
            name=_job_path(JOBS["harvest"]),
            overrides=run_v2.RunJobRequest.Overrides(
                container_overrides=[override], task_count=min(5, len(sids))))
        client.run_job(request=req)
        return {"ok": True, "message": f"re-harvesting {len(sids)} pub(s) (--force, {min(5, len(sids))} shards)"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"{type(e).__name__}: {e}"}


def attention_pubs(limit: int = 200) -> dict:
    """Publications whose latest harvest log is category=attention — the failures worth eyes. One
    row per series_id (most recent), newest first. {ok, pubs, message?}."""
    if settings.JOBS_DRY_RUN:
        return {"ok": True, "dry_run": True, "pubs": [],
                "message": "DRY-RUN — pubs needing attention appear here after a real run."}
    try:
        client = _logging_client()
        flt = (f'resource.type="cloud_run_job" resource.labels.job_name="{HARVEST_JOB}" '
               f'jsonPayload.category="attention"')
        seen: dict[str, dict] = {}
        for e in client.list_entries(filter_=flt, order_by="timestamp desc",
                                     page_size=limit, max_results=limit):
            p = e.payload if isinstance(e.payload, dict) else {}
            sid = p.get("series_id", "")
            if sid and sid not in seen:  # newest first → first seen is the latest
                seen[sid] = {
                    "id": sid,
                    "series_code": series_code(sid),
                    "step": p.get("step", ""),
                    "text": p.get("message", ""),
                    "time": e.timestamp.isoformat() if e.timestamp else "",
                }
        pubs = list(seen.values())
        # A pub that's since been harvested (COG now in the bucket) is no longer attention — a
        # successful re-run clears it, even though the old "attention" log line still exists.
        try:
            from . import stac
            done = stac.harvested_ids()
            pubs = [p for p in pubs if p["id"].strip().upper() not in done]
        except Exception:  # noqa: BLE001 — bucket unreadable → show the raw list rather than nothing
            pass
        return {"ok": True, "pubs": pubs}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "pubs": [], "message": f"{type(e).__name__}: {e}"}
