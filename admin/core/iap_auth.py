"""Google Cloud IAP auth — stateless authorization completely delegated to GCP IAM.

In prod, IAP verifies the user and validates their IAM role bindings (e.g., IAP-Secured Web App
User) before the request reaches Cloud Run, injecting X-Goog-Authenticated-User-Email. Any
non-empty email therefore represents an authenticated and IAM-authorized administrator.

In DEBUG, `DEV_IAP_EMAIL` stands in. Gate views with `@admin_required`.
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
        # Authorization is entirely delegated to GCP IAM role bindings (e.g., IAP-Secured Web App User)
        request.is_admin = bool(email)
        return self.get_response(request)


def admin_required(view):
    """Allow any IAP-authenticated user (authorized via GCP IAM)."""
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not getattr(request, "is_admin", False):
            who = getattr(request, "iap_email", "") or "anonymous"
            return HttpResponseForbidden(
                f"Not authorized ({who}). Ensure you are granted the 'IAP-Secured Web App User' "
                f"IAM role on the ugs-warehouse-admin service in Google Cloud Console."
            )
        return view(request, *args, **kwargs)
    return wrapped


def context_processor(request):
    from django.conf import settings
    return {
        "iap_email": getattr(request, "iap_email", ""),
        "is_admin": getattr(request, "is_admin", False),
        "VIEWER_BASE": settings.VIEWER_BASE,
    }
