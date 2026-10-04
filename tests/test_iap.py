"""The review services' IAP identity: audience derivation, key caching, and what counts as a token."""
import base64
import json

import pytest

from ugs_warehouse import iap


class _RegionResp:
    text = "projects/534590904912/regions/us-central1\n"

    def raise_for_status(self):
        pass


def test_audience_is_derived_from_the_metadata_server(monkeypatch):
    # A wrong aud would reject every real IAP user.
    seen = {}

    def fake_get(url, headers=None, timeout=None):
        seen["url"], seen["headers"] = url, headers
        return _RegionResp()

    monkeypatch.delenv("IAP_AUDIENCE", raising=False)
    monkeypatch.setenv("K_SERVICE", "ugs-warehouse-review-serving")
    monkeypatch.setattr(iap.requests, "get", fake_get)
    iap._audience.cache_clear()
    try:
        aud = iap._audience()
    finally:
        iap._audience.cache_clear()
    assert aud == "/projects/534590904912/locations/us-central1/services/ugs-warehouse-review-serving"
    assert seen["url"].endswith("/computeMetadata/v1/instance/region")
    assert seen["headers"] == {"Metadata-Flavor": "Google"}


def test_no_audience_off_cloud_run(monkeypatch):
    monkeypatch.delenv("IAP_AUDIENCE", raising=False)
    monkeypatch.delenv("K_SERVICE", raising=False)
    iap._audience.cache_clear()
    try:
        assert iap._audience() is None
    finally:
        iap._audience.cache_clear()


def test_no_identity_without_an_audience(monkeypatch, fake_iap):
    fake_iap.install(monkeypatch, iap)
    monkeypatch.setattr(iap, "_audience", lambda: None)
    assert iap.verified_email({iap.JWT_HEADER: fake_iap.token("a@utah.gov")}) == ""


def test_only_es256_tokens_are_considered(monkeypatch, fake_iap):
    fake_iap.install(monkeypatch, iap)
    header, rest = fake_iap.token("a@utah.gov").split(".", 1)
    hs256 = base64.urlsafe_b64encode(json.dumps({"alg": "HS256", "kid": "test-kid"}).encode())
    assert iap.verified_email({iap.JWT_HEADER: f"{hs256.rstrip(b'=').decode()}.{rest}"}) == ""
    assert iap.verified_email({iap.JWT_HEADER: f"{header}.{rest}"}) == "a@utah.gov"


def test_whoami_shows_only_a_verified_identity(monkeypatch, fake_iap):
    serve = pytest.importorskip("ugs_warehouse.serve")
    from fastapi import HTTPException

    class _Req:
        def __init__(self, headers):
            self.headers = headers

    fake_iap.install(monkeypatch, iap)
    who = serve.whoami(_Req({iap.JWT_HEADER: fake_iap.token("reviewer@utah.gov")}))
    assert who == {"email": "reviewer@utah.gov", "user": "reviewer"}
    forged = {"x-goog-authenticated-user-email": "accounts.google.com:reviewer@utah.gov"}
    with pytest.raises(HTTPException) as e:
        serve.whoami(_Req(forged))
    assert e.value.status_code == 404


def test_keys_go_through_an_http_cache():
    # gstatic's Cache-Control decides how long keys are kept; without the cache every request refetches.
    from cachecontrol.adapter import CacheControlAdapter
    assert isinstance(iap.cached_request.session.get_adapter(iap._CERTS_URL), CacheControlAdapter)
