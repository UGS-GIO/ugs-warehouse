from django.shortcuts import render
from django.views.decorators.http import require_POST

from core.iap_auth import admin_required

from . import jobs, stac


@admin_required
def dashboard(request):
    return render(request, "ops/dashboard.html", {
        "jobs": list(jobs.JOBS.values()),
        "coverage": stac.cog_coverage(),
        "topics": stac.serving_topics(),
        "dry_run": __import__("django.conf", fromlist=["settings"]).settings.JOBS_DRY_RUN,
    })


@admin_required
@require_POST
def trigger(request, key):
    result = jobs.run(key)
    return render(request, "ops/_job_result.html", {
        "key": key, "job": jobs.JOBS.get(key), "result": result, "recent": jobs.recent(key),
    })


@admin_required
def job_status(request, key):
    return render(request, "ops/_job_status.html", {"key": key, "recent": jobs.recent(key)})


@admin_required
def coverage(request):
    return render(request, "ops/_coverage.html", {"coverage": stac.cog_coverage()})


@admin_required
def publications_registry(request):
    search = request.GET.get("q", "")
    status = request.GET.get("status", "")
    series = request.GET.get("series", "")

    # Get full list of calculated statuses
    rows = stac.get_harvest_status(search_query=search, status_filter=status, series_filter=series)
    total_count = len(rows)

    # Paginate/Limit to first 100 rows for lightning-fast HTML rendering
    display_rows = rows[:100]

    # Dynamically discover all unique series codes from the publications DB
    from ugs_warehouse.pubs.sink_stac import series_code
    try:
        from ugs_warehouse.pubs import source
        pubs = source.read_pubs()
        series_codes = sorted(list(set(series_code(p.get("series_id") or "") for p in pubs if p.get("series_id"))))
        series_codes = [c for c in series_codes if c]
    except Exception:
        series_codes = []

    context = {
        "rows": display_rows,
        "total_count": total_count,
        "limit": 100,
        "series_codes": series_codes,
        "q": search,
        "status_filter": status,
        "series_filter": series,
    }

    if request.htmx:
        return render(request, "ops/_publications_table.html", context)

    return render(request, "ops/publications.html", context)
