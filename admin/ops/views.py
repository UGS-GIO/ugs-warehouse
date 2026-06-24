from django.conf import settings
from django.shortcuts import render
from django.views.decorators.http import require_POST

from core.iap_auth import admin_required

from . import jobs, stac


@admin_required
def dashboard(request):
    # Group jobs into pipeline stages (mirrors the Architecture page) so the console reads as the
    # data flow, not a flat button list.
    stages = [{**s, "jobs": [jobs.JOBS[k] for k in s["jobs"] if k in jobs.JOBS]} for s in jobs.STAGES]
    return render(request, "ops/dashboard.html", {
        "stages": stages,
        "coverage": stac.cog_coverage(),
        "topics": stac.serving_topics(),
        "dry_run": settings.JOBS_DRY_RUN,
    })


@admin_required
@require_POST
def trigger(request, key):
    result = jobs.run(key)
    return render(request, "ops/_job_result.html", {
        "key": key, "job": jobs.JOBS.get(key), "result": result,
        "recent": jobs.recent(key), "console_url": jobs.console_logs_url(key),
    })


@admin_required
def job_status(request, key):
    return render(request, "ops/_job_status.html", {"key": key, "recent": jobs.recent(key)})


@admin_required
def job_logs(request, key):
    """Near-live tail of a job's logs — the in-app feed shown after a trigger (any job)."""
    return render(request, "ops/_job_logs.html", {
        "key": key, "job": jobs.JOBS.get(key),
        "log": jobs.logs(key), "console_url": jobs.console_logs_url(key),
    })


@admin_required
def coverage(request):
    return render(request, "ops/_coverage.html", {"coverage": stac.cog_coverage()})


@admin_required
def publications_registry(request):
    search = request.GET.get("q", "")
    status = request.GET.get("status", "")
    series = request.GET.get("series", "")

    # One cached read backs both the rows and the series dropdown (no double pub-metadata read).
    # A read failure (e.g. ugs_warehouse not importable / no bucket access) surfaces as `error`
    # instead of a silent empty table — the point of an ops console is to show what's wrong.
    error = ""
    rows, series_codes = [], []
    try:
        rows = stac.get_harvest_status(search_query=search, status_filter=status, series_filter=series)
        series_codes = stac.harvest_series_codes()
    except Exception as e:  # noqa: BLE001
        error = f"{type(e).__name__}: {e}"

    context = {
        "rows": rows[:100],
        "total_count": len(rows),
        "limit": 100,
        "series_codes": series_codes,
        "error": error,
        "q": search,
        "status_filter": status,
        "series_filter": series,
    }
    template = "ops/_publications_table.html" if request.htmx else "ops/publications.html"
    return render(request, template, context)


@admin_required
def pub_logs(request, series_id):
    """Per-pub harvest log report (one publication's structured log timeline)."""
    return render(request, "ops/_pub_logs.html", {
        "series_id": series_id, "log": jobs.pub_logs(series_id),
    })
