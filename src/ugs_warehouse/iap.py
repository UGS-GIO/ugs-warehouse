"""The caller's identity from Cloud Run IAP, taken only from IAP's signed JWT.

IAP also sends `X-Goog-Authenticated-User-Email`, but that header is only trustworthy if nothing can
reach the service except through IAP, and anyone can set it. The signed `X-Goog-IAP-JWT-Assertion`
holds regardless: it is verified against Google's IAP keys, the IAP issuer, and THIS service's
audience, so a token IAP minted for another service (or none at all) yields no identity.
See https://cloud.google.com/iap/docs/signed-headers-howto.

The admin console (admin/core/iap_auth.py) uses this same check.
"""
from __future__ import annotations

import functools
import logging
import os

import cachecontrol
import google.auth.transport.requests
import requests
from google.oauth2 import id_token

log = logging.getLogger("ugs-warehouse.iap")

JWT_HEADER = "x-goog-iap-jwt-assertion"
_CERTS_URL = "https://www.gstatic.com/iap/verify/public_key"
_ISSUER = "https://cloud.google.com/iap"
_METADATA = "http://metadata.google.internal/computeMetadata/v1/"
_CLOCK_SKEW_S = 30  # Google's guide allows 30 s between IAP's clock and ours

# Google's signing keys (IAP here, Firebase in comments.py) are kept as long as their Cache-Control
# allows, then refetched.
cached_request = google.auth.transport.requests.Request(session=cachecontrol.CacheControl(requests.Session()))


@functools.cache
def _audience() -> str | None:
    """`/projects/NUMBER/locations/REGION/services/NAME`, the aud IAP stamps for direct Cloud Run IAP.
    IAP_AUDIENCE overrides; off Cloud Run with no override there is no audience, so no IAP identity."""
    if aud := os.environ.get("IAP_AUDIENCE"):
        return aud
    service = os.environ.get("K_SERVICE")
    if not service:
        return None
    resp = requests.get(_METADATA + "instance/region", headers={"Metadata-Flavor": "Google"}, timeout=5)
    resp.raise_for_status()
    _, number, _, region = resp.text.strip().split("/")  # projects/NUMBER/regions/REGION
    return f"/projects/{number}/locations/{region}/services/{service}"


def verified_email(headers) -> str:
    """The IAP user's email as IAP states it, or "" if the request carries no valid IAP token for this
    service. Never raises: a bad token is an unauthenticated request, not a 500."""
    token = headers.get(JWT_HEADER)
    if not token:
        return ""
    try:
        audience = _audience()
        if audience is None:
            log.warning("IAP token present but no audience (not on Cloud Run, IAP_AUDIENCE unset)")
            return ""
        claims = id_token.verify_token(token, cached_request, audience=audience, certs_url=_CERTS_URL,
                                       clock_skew_in_seconds=_CLOCK_SKEW_S)
    except Exception:  # noqa: BLE001 (expired/forged/wrong-audience token, or keys/metadata unreachable)
        log.warning("IAP token verification failed", exc_info=True)
        return ""
    if claims.get("iss") != _ISSUER:
        log.warning("IAP token has unexpected issuer %r", claims.get("iss"))
        return ""
    return claims.get("email") or ""
