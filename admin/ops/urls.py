from django.urls import path

from . import views

app_name = "ops"
urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("tab/run", views.tab_run, name="tab_run"),
    path("tab/data", views.tab_data, name="tab_data"),
    path("jobs/<str:key>/run", views.trigger, name="trigger"),
    path("jobs/<str:key>/status", views.job_status, name="job_status"),
    path("jobs/<str:key>/logs", views.job_logs, name="job_logs"),
    path("jobs/<str:key>/cancel", views.cancel, name="cancel"),
    path("coverage", views.coverage, name="coverage"),
    path("whats-next", views.health, name="health"),  # NB: not /health — that's the Cloud Run probe
    path("executions", views.executions, name="executions"),
    path("service-health", views.service_health, name="service_health"),
    path("attention", views.attention, name="attention"),
    path("attention/reharvest", views.attention_reharvest, name="attention_reharvest"),
    path("publications", views.publications_registry, name="publications"),
    path("publications/<path:series_id>/logs", views.pub_logs, name="pub_logs"),
    path("publications/<path:series_id>/reharvest", views.reharvest, name="reharvest"),
    path("jobs/mosaics/tier/<str:tier>", views.rebuild_mosaic, name="rebuild_mosaic"),
    path("contents", views.contents_list, name="contents"),
    path("contents/row", views.contents_row, name="contents_row"),
    path("contents/<path:series_id>/edit", views.contents_edit, name="contents_edit"),
    path("contents/<path:series_id>/save", views.contents_save, name="contents_save"),
]
