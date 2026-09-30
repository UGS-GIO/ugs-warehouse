"""Comment write-authorization: reads are open to any authenticated user; writes (_require_editor) are
limited to IAP-gated requests (the review group) or bearer callers in the editor allow-list."""
import pytest
from fastapi import HTTPException

from ugs_warehouse import comments, iap


class _Req:
    def __init__(self, headers):
        self.headers = headers


class _IapReq(_Req):
    """An IAP-gated request: carries IAP's signed JWT."""
    def __init__(self, token):
        super().__init__({iap.JWT_HEADER: token})


class _BearerReq:
    """A hazards-review request: no IAP header; identity comes from a (here, pre-verified) bearer."""
    def __init__(self, email):
        self._email = email
        self.headers = {"authorization": "Bearer fake"}


@pytest.fixture(autouse=True)
def _stub_bearer(monkeypatch, fake_iap):
    # Bypass real Firebase verification: the bearer's email is whatever _BearerReq carries.
    monkeypatch.setattr(comments, "_verify_firebase_email", lambda _t: None)
    fake_iap.install(monkeypatch, iap)


def test_iap_request_is_always_allowed_to_write(fake_iap):
    # IAP already gated to the review group → editor.
    req = _IapReq(fake_iap.token("reviewer@utah.gov"))
    assert comments._require_editor(req) == "reviewer@utah.gov"


def test_forged_iap_email_header_is_not_an_identity():
    # Anyone can send the plain header; only IAP's signed JWT counts.
    req = _Req({"x-goog-authenticated-user-email": "accounts.google.com:reviewer@utah.gov"})
    with pytest.raises(HTTPException) as e:
        comments._require_editor(req)
    assert e.value.status_code == 401


def test_iap_token_for_wrong_audience_issuer_or_expired_is_rejected(fake_iap, rejected_token_kwargs):
    req = _IapReq(fake_iap.token("reviewer@utah.gov", **rejected_token_kwargs))
    with pytest.raises(HTTPException) as e:
        comments._require_editor(req)
    assert e.value.status_code == 401


def test_iap_token_signed_by_another_key_is_rejected(other_iap):
    req = _IapReq(other_iap.token("reviewer@utah.gov"))
    with pytest.raises(HTTPException) as e:
        comments._require_editor(req)
    assert e.value.status_code == 401


def test_bearer_write_denied_when_not_allowlisted(monkeypatch):
    monkeypatch.setattr(comments, "_EDITOR_EMAILS", set())
    monkeypatch.setattr(comments, "_EDITOR_DOMAINS", set())
    monkeypatch.setattr(comments, "_verify_firebase_email", lambda _t: "someone@utah.gov")
    with pytest.raises(HTTPException) as e:
        comments._require_editor(_BearerReq("someone@utah.gov"))
    assert e.value.status_code == 403  # fail-closed: unconfigured → bearer writes rejected


def test_bearer_write_allowed_by_email_or_domain(monkeypatch):
    monkeypatch.setattr(comments, "_EDITOR_EMAILS", {"editor@utah.gov"})
    monkeypatch.setattr(comments, "_EDITOR_DOMAINS", {"geology.utah.gov"})
    monkeypatch.setattr(comments, "_verify_firebase_email", lambda _t: "editor@utah.gov")
    assert comments._require_editor(_BearerReq("editor@utah.gov")) == "editor@utah.gov"
    monkeypatch.setattr(comments, "_verify_firebase_email", lambda _t: "x@geology.utah.gov")
    assert comments._require_editor(_BearerReq("x@geology.utah.gov")) == "x@geology.utah.gov"
    monkeypatch.setattr(comments, "_verify_firebase_email", lambda _t: "nope@gmail.com")
    with pytest.raises(HTTPException):
        comments._require_editor(_BearerReq("nope@gmail.com"))


def test_invalid_iap_token_falls_back_to_the_bearer_allow_list(monkeypatch, other_iap):
    monkeypatch.setattr(comments, "_EDITOR_EMAILS", set())
    monkeypatch.setattr(comments, "_EDITOR_DOMAINS", set())
    monkeypatch.setattr(comments, "_verify_firebase_email", lambda _t: "someone@utah.gov")
    req = _Req({iap.JWT_HEADER: other_iap.token("reviewer@utah.gov"), "authorization": "Bearer x"})
    with pytest.raises(HTTPException) as e:
        comments._require_editor(req)
    assert e.value.status_code == 403  # a bad IAP token earns no IAP trust


def test_unauthenticated_is_401(monkeypatch):
    monkeypatch.setattr(comments, "_verify_firebase_email", lambda _t: None)

    class _NoAuth:
        headers = {}
    with pytest.raises(HTTPException) as e:
        comments._require_editor(_NoAuth())
    assert e.value.status_code == 401
