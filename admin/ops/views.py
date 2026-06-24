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
