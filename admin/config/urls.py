from django.urls import include, path

# /health is served by core.health.HealthCheckMiddleware (ahead of host validation), not a view.
urlpatterns = [path("", include("ops.urls"))]
