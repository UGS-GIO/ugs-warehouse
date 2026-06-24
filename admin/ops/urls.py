from django.urls import path

from . import views

app_name = "ops"
urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("jobs/<str:key>/run", views.trigger, name="trigger"),
    path("jobs/<str:key>/status", views.job_status, name="job_status"),
    path("coverage", views.coverage, name="coverage"),
    path("publications", views.publications_registry, name="publications"),
]
