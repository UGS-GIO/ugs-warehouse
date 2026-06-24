from django.urls import path

from . import views

app_name = "ops"
urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("jobs/<str:key>/run", views.trigger, name="trigger"),
    path("jobs/<str:key>/status", views.job_status, name="job_status"),
    path("jobs/<str:key>/logs", views.job_logs, name="job_logs"),
    path("coverage", views.coverage, name="coverage"),
    path("publications", views.publications_registry, name="publications"),
    path("publications/<path:series_id>/logs", views.pub_logs, name="pub_logs"),
]
