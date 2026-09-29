"""Google Cloud IAP auth — stateless authorization completely delegated to GCP IAM.

In prod, IAP verifies the user and validates their IAM role bindings (e.g., IAP-Secured Web App
User) before the request reaches Cloud Run. The identity is taken only from IAP's signed JWT
(`X-Goog-IAP-JWT-Assertion`), verified against Google's IAP keys, the IAP issuer and this service's
audience. The plain `X-Goog-Authenticated-User-Email` header is never read: anyone can set it, so it
is only as safe as the guarantee that nothing reaches the service except through IAP.
See https://cloud.google.com/iap/docs/signed-headers-howto.

Mirrors src/ugs_warehouse/iap.py (this app ships as its own image without that package).

In DEBUG, `DEV_IAP_EMAIL` stands in. Gate views with `@admin_required`.
"""
import functools
import logging
import math
import os
import threading
import time
from functools import wraps

import requests
from django.conf import settings
from django.http import HttpResponseForbidden
from google.auth import jwt

log = logging.getLogger(__name__)

_JWT_HEADER = "HTTP_X_GOOG_IAP_JWT_ASSERTION"
_CERTS_URL = "https://www.gstatic.com/iap/verify/public_key"
_ISSUER = "https://cloud.google.com/iap"
_METADATA = "http://metadata.google.internal/computeMetadata/v1/"
_CERTS_TTL_S = 3600
_REFRESH_MIN_S = 60  # gstatic is asked at most this often, so forged tokens or an outage can't stall us
_CLOCK_SKEW_S = 30  # Google's guide allows 30 s between IAP's clock and ours

_lock = threading.Lock()
_certs: dict[str, str] = {}
_certs_at = -math.inf    # when the keys we hold were fetched
_attempt_at = -math.inf  # when we last asked gstatic, successful or not


def _iap_certs(refresh: bool = False) -> dict[str, str]:
    """Google's IAP public keys by key id. `refresh` asks for a refetch (a token signed with a key we
    don't hold yet, since IAP rotates keys); either way gstatic is asked at most every _REFRESH_MIN_S,
    and a failed fetch keeps serving the keys already held rather than rejecting every user."""
    global _certs, _certs_at, _attempt_at
    with _lock:
        now = time.monotonic()
        wanted = refresh or not _certs or now - _certs_at > _CERTS_TTL_S
        if wanted and now - _attempt_at > _REFRESH_MIN_S:
            _attempt_at = now
            try:
                resp = requests.get(_CERTS_URL, timeout=5)
                resp.raise_for_status()
                _certs, _certs_at = resp.json(), now
            except Exception:
                if not _certs:
                    raise
                log.warning("IAP key refresh failed; keeping the keys already fetched", exc_info=True)
        if not _certs:
            raise RuntimeError("IAP public keys unavailable (last fetch failed)")
        return _certs


@functools.cache
def _audience() -> str | None:
    """`/projects/NUMBER/locations/REGION/services/NAME`, the aud IAP stamps for direct Cloud Run IAP.
    IAP_AUDIENCE overrides; off Cloud Run with no override there is no audience, so no IAP identity."""
    if settings.IAP_AUDIENCE:
        return settings.IAP_AUDIENCE
    service = os.environ.get("K_SERVICE")
    if not service:
        return None
    resp = requests.get(_METADATA + "instance/region", headers={"Metadata-Flavor": "Google"}, timeout=5)
    resp.raise_for_status()
    _, number, _, region = resp.text.strip().split("/")  # projects/NUMBER/regions/REGION
    return f"/projects/{number}/locations/{region}/services/{service}"


def _decode(token: str, audience: str) -> dict:
    # IAP signs with ES256 only; refuse anything else before choosing a verifier by `alg`.
    if jwt.decode_header(token).get("alg") != "ES256":
        raise ValueError("IAP token is not ES256")
    try:
        return jwt.decode(token, certs=_iap_certs(), audience=audience,
                          clock_skew_in_seconds=_CLOCK_SKEW_S)
    except ValueError:
        # IAP rotates keys; a token signed with one we haven't fetched yet is retried once.
        return jwt.decode(token, certs=_iap_certs(refresh=True), audience=audience,
                          clock_skew_in_seconds=_CLOCK_SKEW_S)


def verified_email(meta) -> str:
    """The IAP user's email as IAP states it, or "" if the request carries no valid IAP token for this
    service. Never raises: a bad token is an unauthenticated request, not a 500."""
    token = meta.get(_JWT_HEADER)
    if not token:
        return ""
    try:
        audience = _audience()
        if audience is None:
            log.warning("IAP token present but no audience (not on Cloud Run, IAP_AUDIENCE unset)")
            return ""
        claims = _decode(token, audience)
    except Exception:  # noqa: BLE001 (expired/forged/wrong-audience token, or keys/metadata unreachable)
        log.warning("IAP token verification failed", exc_info=True)
        return ""
    if claims.get("iss") != _ISSUER:
        log.warning("IAP token has unexpected issuer %r", claims.get("iss"))
        return ""
    return claims.get("email") or ""


class IAPAuthMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        email = verified_email(request.META).lower()
        if not email and settings.DEBUG and settings.DEV_IAP_EMAIL:
            email = settings.DEV_IAP_EMAIL.lower()
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
