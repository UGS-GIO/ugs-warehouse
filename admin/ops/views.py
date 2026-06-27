from django.conf import settings
from django.shortcuts import render
from django.views.decorators.http import require_POST

from core.iap_auth import admin_required

from . import contents, jobs, stac


@admin_required
def dashboard(request):
    # Lean shell — the tabs (Run/Watch/Data) load their own content, so the slow STAC/GCS reads only
    # happen when you open the Data tab. Keeps the dashboard instant.
    return render(request, "ops/dashboard.html", {"dry_run": settings.JOBS_DRY_RUN})


@admin_required
def tab_run(request):
    """Run tab — the pipeline stages + job triggers (the 'act' surface)."""
    stages = [{**s, "jobs": [jobs.JOBS[k] for k in s["jobs"] if k in jobs.JOBS]} for s in jobs.STAGES]
    return render(request, "ops/_run.html", {"stages": stages})


@admin_required
def tab_data(request):
    """Data tab — coverage, attention, serving topics (the 'look' surface)."""
    return render(request, "ops/_data.html", {
        "coverage": stac.cog_coverage(),
        "harvested": stac.harvested_bucket_count(),
        "topics": stac.serving_topics(),
    })


@admin_required
@require_POST
def cancel(request, key):
    result = jobs.cancel(request.POST.get("execution", ""))
    return render(request, "ops/_job_result.html", {
        "key": key, "job": jobs.JOBS.get(key), "result": result,
        "recent": jobs.recent(key), "console_url": jobs.console_logs_url(key),
    })


@admin_required
@require_POST
def reharvest(request, series_id):
    return render(request, "ops/_reharvest_result.html",
                  {"series_id": series_id, "result": jobs.reharvest_one(series_id)})


@admin_required
def attention(request):
    return render(request, "ops/_attention.html", {"att": jobs.attention_pubs()})


@admin_required
@require_POST
def attention_reharvest(request):
    """Re-harvest only the pubs currently in the attention list (re-queried server-side)."""
    ids = [p["id"] for p in jobs.attention_pubs().get("pubs", [])]
    return render(request, "ops/_attention_reharvest_result.html",
                  {"result": jobs.reharvest_many(ids)})


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
def executions(request):
    """Unified executions feed across all jobs (newest first) + what's running now."""
    ex = jobs.all_executions()
    return render(request, "ops/_executions.html", {
        "executions": ex,
        "running": [e for e in ex if e["state"] == "running"],
    })


@admin_required
def service_health(request):
    """Up/down lights for the public serving surfaces."""
    return render(request, "ops/_service_health.html", {"checks": stac.service_health()})


@admin_required
def health(request):
    """'What to run next' — aggregate the actionable gaps so one glance shows the next action.
    Loaded async (hx-get on load) so it never blocks the dashboard."""
    cov = stac.cog_coverage()
    hv = stac.harvested_bucket_count()
    topics = stac.serving_topics()
    att = jobs.attention_pubs()
    try:
        rows = stac.get_harvest_status()
        pending = sum(1 for r in rows if r["status"] == "pending")  # has a zip, no COG yet
    except Exception:  # noqa: BLE001
        pending = None
    bucket = hv.get("count") if hv.get("ok") else None
    bound = cov.get("total_cogs")
    unbound = bucket - bound if (bucket is not None and bound is not None and bucket > bound) else 0
    return render(request, "ops/_health.html", {
        "pending": pending,                                   # → run Harvest COGs
        "unbound": unbound,                                   # → run Rebuild pubs STAC
        "unstyled": sum(1 for t in topics if not t.get("styled")),  # → author a style in ugs-styles
        "attention": len(att["pubs"]) if att.get("ok") else None,   # → re-harvest / investigate
    })


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


# ---- Survey Notes "In this issue" sidecar editor ----

@admin_required
def contents_list(request):
    """Survey Notes issues + TOC-sidecar status; pick one to author/correct its contents."""
    search = request.GET.get("q", "")
    error, rows = "", []
    try:
        rows = contents.list_issues(search)
    except Exception as e:  # noqa: BLE001 — show why it's empty rather than a blank table
        error = f"{type(e).__name__}: {e}"
    return render(request, "ops/contents_list.html", {"rows": rows, "q": search, "error": error})


@admin_required
def contents_edit(request, series_id):
    """Editor for one issue — embedded PDF + editable {title, page} rows (pre-filled from sidecar)."""
    return render(request, "ops/_contents_editor.html", {"c": contents.load(series_id)})


@admin_required
@require_POST
def contents_save(request, series_id):
    """Persist the edited rows to the GCS sidecar (source=manual). Parallel title[]/page[] fields."""
    titles = request.POST.getlist("title")
    pages = request.POST.getlist("page")
    entries = [{"title": t, "page": p} for t, p in zip(titles, pages)]
    try:
        result = contents.save(series_id, entries)
        return render(request, "ops/_contents_saved.html", {"series_id": series_id, "result": result})
    except Exception as e:  # noqa: BLE001
        return render(request, "ops/_contents_saved.html",
                      {"series_id": series_id, "error": f"{type(e).__name__}: {e}"})


@admin_required
def contents_row(request):
    """One blank editable row (HTMX 'add row')."""
    return render(request, "ops/_contents_row.html", {"e": {"title": "", "page": ""}})
