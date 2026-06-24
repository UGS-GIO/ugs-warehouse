"""Google Cloud IAP auth — adapted from the UCRC app, but stateless (no Django users).

In prod, IAP verifies the user before the request reaches Django and injects
`X-Goog-Authenticated-User-Email`. This middleware reads it onto `request.iap_email` and sets
`request.is_admin` from the `ADMIN_EMAILS` allowlist. In DEBUG, `DEV_IAP_EMAIL` stands in.

There is no login page and no user table — authN is IAP, authZ is the allowlist. Gate views with
`@admin_required`.
"""
from functools import wraps

from django.conf import settings
from django.http import HttpResponseForbidden

_IAP_HEADER = "HTTP_X_GOOG_AUTHENTICATED_USER_EMAIL"
_IAP_PREFIX = "accounts.google.com:"


class IAPAuthMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        raw = request.META.get(_IAP_HEADER, "")
        if raw:
            email = raw.removeprefix(_IAP_PREFIX).lower()
        elif settings.DEBUG and settings.DEV_IAP_EMAIL:
            email = settings.DEV_IAP_EMAIL.lower()
        else:
            email = ""
        request.iap_email = email
        # Empty allowlist in DEBUG = allow the dev user (convenience); in prod an empty allowlist
        # locks everyone out (fail-closed).
        allow = set(settings.ADMIN_EMAILS)
        request.is_admin = bool(email) and (email in allow or (settings.DEBUG and not allow))
        return self.get_response(request)


def admin_required(view):
    """Allow only IAP-verified emails on the ADMIN_EMAILS allowlist."""
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not getattr(request, "is_admin", False):
            who = getattr(request, "iap_email", "") or "anonymous"
            return HttpResponseForbidden(f"Not authorized ({who}). Ask to be added to ADMIN_EMAILS.")
        return view(request, *args, **kwargs)
    return wrapped


def context_processor(request):
    return {
        "iap_email": getattr(request, "iap_email", ""),
        "is_admin": getattr(request, "is_admin", False),
    }
