from django.urls import path

from . import views

app_name = "ops"
urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("jobs/<str:key>/run", views.trigger, name="trigger"),
    path("jobs/<str:key>/status", views.job_status, name="job_status"),
    path("jobs/<str:key>/logs", views.job_logs, name="job_logs"),
    path("jobs/<str:key>/cancel", views.cancel, name="cancel"),
    path("coverage", views.coverage, name="coverage"),
    path("whats-next", views.health, name="health"),  # NB: not /health — that's the Cloud Run probe
    path("attention", views.attention, name="attention"),
    path("attention/reharvest", views.attention_reharvest, name="attention_reharvest"),
    path("publications", views.publications_registry, name="publications"),
    path("publications/<path:series_id>/logs", views.pub_logs, name="pub_logs"),
    path("publications/<path:series_id>/reharvest", views.reharvest, name="reharvest"),
]
