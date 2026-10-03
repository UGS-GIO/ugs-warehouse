"""The review services' IAP identity: audience derivation, key caching, and what counts as a token."""
import base64
import json
import math

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
    monkeypatch.setattr(iap, "_iap_certs", lambda refresh=False: fake_iap.certs)
    monkeypatch.setattr(iap, "_audience", lambda: None)
    assert iap.verified_email({iap.JWT_HEADER: fake_iap.token("a@utah.gov")}) == ""


class _CertsResp:
    def __init__(self, certs=None, fail=False):
        self._certs, self._fail = certs, fail

    def raise_for_status(self):
        if self._fail:
            raise iap.requests.HTTPError("503")

    def json(self):
        return self._certs


@pytest.fixture
def fresh_certs(monkeypatch):
    monkeypatch.setattr(iap, "_certs", {})
    monkeypatch.setattr(iap, "_certs_at", -math.inf)
    monkeypatch.setattr(iap, "_attempt_at", -math.inf)


def test_keys_survive_a_failed_refresh_and_back_off(monkeypatch, fresh_certs):
    clock = [1000.0]
    calls = []
    responses = [_CertsResp({"k1": "pem"}), _CertsResp(fail=True)]

    def fake_get(url, timeout=None):
        calls.append(url)
        return responses[min(len(calls), len(responses)) - 1]

    monkeypatch.setattr(iap.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(iap.requests, "get", fake_get)

    assert iap._iap_certs() == {"k1": "pem"}
    clock[0] += iap._CERTS_TTL_S + 1                 # keys expire; gstatic is down
    assert iap._iap_certs() == {"k1": "pem"}         # the keys we hold still verify
    assert iap._iap_certs() == {"k1": "pem"}         # ...without refetching on every request
    assert len(calls) == 2
    assert iap._iap_certs(refresh=True) == {"k1": "pem"}  # a bad token's retry can't refetch either
    assert len(calls) == 2
    clock[0] += iap._REFRESH_MIN_S + 1
    iap._iap_certs()
    assert len(calls) == 3


def test_first_key_fetch_failure_is_not_hidden_and_not_retried_per_request(monkeypatch, fresh_certs):
    calls = []

    def fake_get(url, timeout=None):
        calls.append(url)
        return _CertsResp(fail=True)

    monkeypatch.setattr(iap.requests, "get", fake_get)
    with pytest.raises(iap.requests.HTTPError):
        iap._iap_certs()
    with pytest.raises(RuntimeError):
        iap._iap_certs(refresh=True)
    assert len(calls) == 1


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


def test_a_bad_token_for_a_known_key_does_not_refetch_keys(monkeypatch, fake_iap, other_iap):
    refreshes = []

    def certs(refresh=False):
        refreshes.append(refresh)
        return fake_iap.certs

    monkeypatch.setattr(iap, "_iap_certs", certs)
    monkeypatch.setattr(iap, "_audience", lambda: fake_iap.audience)
    # Same key id, different key: a forged signature, not a rotated key.
    assert iap.verified_email({iap.JWT_HEADER: other_iap.token("a@utah.gov")}) == ""
    assert iap.verified_email({iap.JWT_HEADER: fake_iap.token("a@utah.gov", ttl=-120)}) == ""
    assert refreshes and True not in refreshes


def test_a_rotated_key_is_fetched_once_and_then_verifies(monkeypatch, fake_iap):
    refreshes = []

    def certs(refresh=False):
        refreshes.append(refresh)
        return fake_iap.certs if refresh else {"retired-kid": "pem"}

    monkeypatch.setattr(iap, "_iap_certs", certs)
    monkeypatch.setattr(iap, "_audience", lambda: fake_iap.audience)
    assert iap.verified_email({iap.JWT_HEADER: fake_iap.token("a@utah.gov")}) == "a@utah.gov"
    assert refreshes == [False, True]


def test_fresh_keys_skip_the_lock(monkeypatch):
    class _NoLock:
        def __enter__(self):
            raise AssertionError("took the lock for fresh keys")

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(iap, "_certs", {"k1": "pem"})
    monkeypatch.setattr(iap, "_certs_at", iap.time.monotonic())
    monkeypatch.setattr(iap, "_lock", _NoLock())
    assert iap._iap_certs() == {"k1": "pem"}
