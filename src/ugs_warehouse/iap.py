"""The caller's identity from Cloud Run IAP, taken only from IAP's signed JWT.

IAP also sends `X-Goog-Authenticated-User-Email`, but that header is only trustworthy if nothing can
reach the service except through IAP, and anyone can set it. The signed `X-Goog-IAP-JWT-Assertion`
holds regardless: it is verified against Google's IAP keys, the IAP issuer, and THIS service's
audience, so a token IAP minted for another service (or none at all) yields no identity.
See https://cloud.google.com/iap/docs/signed-headers-howto.

The admin console (admin/core/iap_auth.py) carries its own copy of this check: it ships as a separate
image that does not install this package.
"""
from __future__ import annotations

import functools
import logging
import math
import os
import threading
import time

import requests
from google.auth import jwt

log = logging.getLogger("ugs-warehouse.iap")

JWT_HEADER = "x-goog-iap-jwt-assertion"
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
    if not refresh and _certs and time.monotonic() - _certs_at <= _CERTS_TTL_S:
        return _certs  # the common case: fresh keys, no lock
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
    if aud := os.environ.get("IAP_AUDIENCE"):
        return aud
    service = os.environ.get("K_SERVICE")
    if not service:
        return None
    resp = requests.get(_METADATA + "instance/region", headers={"Metadata-Flavor": "Google"}, timeout=5)
    resp.raise_for_status()
    _, number, _, region = resp.text.strip().split("/")  # projects/NUMBER/regions/REGION
    return f"/projects/{number}/locations/{region}/services/{service}"


def _decode(token: str, audience: str) -> dict:
    header = jwt.decode_header(token)
    # IAP signs with ES256 only; refuse anything else before choosing a verifier by `alg`.
    if header.get("alg") != "ES256":
        raise ValueError("IAP token is not ES256")
    certs = _iap_certs()
    if header.get("kid") not in certs:
        # IAP rotates keys: only a key id we don't hold is worth a refetch, not every bad token.
        certs = _iap_certs(refresh=True)
    return jwt.decode(token, certs=certs, audience=audience, clock_skew_in_seconds=_CLOCK_SKEW_S)


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
        claims = _decode(token, audience)
    except Exception:  # noqa: BLE001 (expired/forged/wrong-audience token, or keys/metadata unreachable)
        log.warning("IAP token verification failed", exc_info=True)
        return ""
    if claims.get("iss") != _ISSUER:
        log.warning("IAP token has unexpected issuer %r", claims.get("iss"))
        return ""
    return claims.get("email") or ""
