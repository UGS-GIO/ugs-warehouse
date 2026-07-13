"""Monitor the warehouse's Cloud Run SERVICES (google-cloud-run v2) — status + live logs, read-only.

Unlike the jobs (which run in the build project), the review serving services live in a DIFFERENT
project (`settings.SERVICES_PROJECT` = ut-dnr-ugs-maps-prod), so both the ServicesClient path and the
Cloud Logging query bind there. Read-only — no deploy/mutate (that's cloudbuild-on-push + tofu); this is
the "is it up / what's erroring" surface so you don't have to open the Cloud Console or shell out.

Auth: Application Default Credentials. In prod the runtime SA needs `roles/run.viewer` +
`roles/logging.viewer` in SERVICES_PROJECT. `JOBS_DRY_RUN` (default in DEBUG) skips the API call.
"""
from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings


@dataclass(frozen=True)
class Service:
    key: str
    name: str          # Cloud Run service name
    label: str
    description: str


# The Cloud Run services worth watching (all in SERVICES_PROJECT).
SERVICES: dict[str, Service] = {s.key: s for s in [
    Service("review-serving", "ugs-warehouse-review-serving", "Review viewer (IAP)",
            "Internal review app — streams the private review bucket + serves the review STAC and the "
            "comments/notifications API. IAP-gated to the nrugsall group."),
    Service("review-api", "ugs-warehouse-review-api", "Review API (token)",
            "Non-IAP twin — /api/* only, Firebase-token auth. Backs the hazards-review app in "
            "ugs-map-viewer (comments synced with the internal viewer)."),
]}


def _service_path(svc: Service) -> str:
    return f"projects/{settings.SERVICES_PROJECT}/locations/{settings.SERVICES_REGION}/services/{svc.name}"


def _logging_client():
    """Cloud Logging client for the services' project (lazy import — importable without GCP libs/creds)."""
    import google.cloud.logging as gcloud_logging
    return gcloud_logging.Client(project=settings.SERVICES_PROJECT)


def status(key: str) -> dict:
    """Current state of a service: ready?, url, latest revision, last-updated. {ok, ...}."""
    svc = SERVICES.get(key)
    if not svc:
        return {"ok": False, "message": f"unknown service {key!r}"}
    if settings.JOBS_DRY_RUN:
        return {"ok": True, "dry_run": True, "ready": True, "url": "https://example.run.app",
                "revision": "(dry-run)", "condition": "DRY-RUN", "updated": ""}
    try:
        from google.cloud import run_v2
        s = run_v2.ServicesClient().get_service(name=_service_path(svc))
        cond = s.terminal_condition
        ready = bool(cond and cond.state == run_v2.Condition.State.CONDITION_SUCCEEDED)
        return {
            "ok": True,
            "ready": ready,
            "url": s.uri,
            "revision": (s.latest_ready_revision or "").split("/")[-1],
            "condition": (cond.message or ("Ready" if ready else cond.reason)) if cond else "",
            "updated": s.update_time.isoformat() if s.update_time else "",
        }
    except Exception as e:  # noqa: BLE001 — surface the error to the operator
        return {"ok": False, "message": f"{type(e).__name__}: {e}"}


def all_status() -> list[dict]:
    """Status of every watched service, for the list view."""
    return [{**status(key), "key": key, "name": svc.name, "label": svc.label,
             "description": svc.description} for key, svc in SERVICES.items()]


def logs(key: str, limit: int = 80) -> dict:
    """Recent Cloud Logging lines for a service (near-live; newest fetched, returned chronological).

    Needs `roles/logging.viewer` on the runtime SA (in SERVICES_PROJECT). Returns {ok, lines, message?}
    so the view shows *why* it's empty instead of a silent blank."""
    svc = SERVICES.get(key)
    if not svc:
        return {"ok": False, "lines": [], "message": f"unknown service {key!r}"}
    if settings.JOBS_DRY_RUN:
        return {"ok": True, "dry_run": True, "lines": [],
                "message": "DRY-RUN — live logs show here against a real deploy."}
    try:
        client = _logging_client()
        flt = f'resource.type="cloud_run_revision" resource.labels.service_name="{svc.name}"'
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
                "revision": labels.get("run.googleapis.com/revision_name", "").split("/")[-1] if labels else "",
                "text": (text or "").rstrip(),
            })
        lines.reverse()  # oldest → newest, like a tail
        return {"ok": True, "lines": lines}
    except Exception as e:  # noqa: BLE001 — surface to the operator
        return {"ok": False, "lines": [], "message": f"{type(e).__name__}: {e}"}


def console_logs_url(key: str) -> str:
    """Deep-link to this service's logs in the Cloud Console (the always-complete view)."""
    svc = SERVICES.get(key)
    if not svc:
        return ""
    return (f"https://console.cloud.google.com/run/detail/"
            f"{settings.SERVICES_REGION}/{svc.name}/logs?project={settings.SERVICES_PROJECT}")
