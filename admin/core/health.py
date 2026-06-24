"""Liveness/startup probe that answers *before* host validation.

Cloud Run's HTTP probe may send a Host header that's a bare IP, which `ALLOWED_HOSTS` rejects with a
400 (DisallowedHost) in `CommonMiddleware` — before any view runs. Registering this first in
MIDDLEWARE short-circuits `/health` ahead of that check, so the probe always gets a clean 200.
"""
from django.http import HttpResponse


class HealthCheckMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path == "/health":
            return HttpResponse("ok", content_type="text/plain")
        return self.get_response(request)
