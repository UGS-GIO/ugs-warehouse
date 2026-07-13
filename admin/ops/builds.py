"""Watch Cloud Build runs (google-cloud-build) — near-live deploy status, read-only.

Builds run in the build project (`settings.GCP_PROJECT` = ut-dnr-ugs-backend-tools). This is the
"is my push building / did it deploy / which step failed" surface, so you watch a deploy in the browser
instead of polling gcloud. `JOBS_DRY_RUN` skips the API. Runtime SA needs `roles/cloudbuild.builds.viewer`.
"""
from __future__ import annotations

from django.conf import settings

# Cloud Build status → (label, tailwind color, is-terminal). Drives the badge + whether to keep polling.
_STATUS = {
    "QUEUED": ("queued", "text-slate-500", False),
    "WORKING": ("working", "text-blue-600", False),
    "SUCCESS": ("success", "text-green-600", True),
    "FAILURE": ("failure", "text-red-600", True),
    "INTERNAL_ERROR": ("internal error", "text-red-600", True),
    "TIMEOUT": ("timeout", "text-amber-600", True),
    "CANCELLED": ("cancelled", "text-slate-500", True),
    "EXPIRED": ("expired", "text-slate-500", True),
}


def _dur(start, end) -> str:
    if not (start and end):
        return ""
    secs = int((end - start).total_seconds())
    if secs < 0:
        return ""
    return f"{secs // 60}m{secs % 60:02d}s" if secs >= 60 else f"{secs}s"


def _failed_step(b) -> str:
    """The id/name of the first failed step (so a failure points you straight at it)."""
    for s in (b.steps or []):
        st = getattr(s, "status", None)
        if st is not None and st.name == "FAILURE":
            return s.id or (s.name.split("/")[-1] if s.name else "")
    return ""


def recent(limit: int = 20) -> dict:
    """Recent Cloud Build runs, newest first. {ok, builds|message}."""
    if settings.JOBS_DRY_RUN:
        return {"ok": True, "dry_run": True, "builds": [],
                "message": "DRY-RUN — live builds show here against real Cloud Build."}
    try:
        from google.cloud.devtools import cloudbuild_v1
        client = cloudbuild_v1.CloudBuildClient()
        out = []
        for b in client.list_builds(project_id=settings.GCP_PROJECT, page_size=limit):
            subs = dict(b.substitutions or {})
            label, color, terminal = _STATUS.get(b.status.name, (b.status.name.lower(), "text-slate-500", True))
            out.append({
                "id": b.id[:8],
                "status": label, "color": color, "terminal": terminal,
                "tag": subs.get("_TAG") or subs.get("SHORT_SHA") or "",
                "created": b.create_time.isoformat() if b.create_time else "",
                "duration": _dur(b.start_time, b.finish_time),
                "log_url": b.log_url,
                "failed_step": _failed_step(b),
                "console_url": f"https://console.cloud.google.com/cloud-build/builds/{b.id}?project={settings.GCP_PROJECT}",
            })
            if len(out) >= limit:
                break
        out.sort(key=lambda x: x["created"], reverse=True)
        # any_active → the template keeps polling fast while something's building.
        return {"ok": True, "builds": out, "any_active": any(not x["terminal"] for x in out)}
    except Exception as e:  # noqa: BLE001 — surface to the operator
        return {"ok": False, "builds": [], "message": f"{type(e).__name__}: {e}"}
