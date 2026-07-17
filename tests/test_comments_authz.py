"""Comment write-authorization: reads are open to any authenticated user; writes (_require_editor) are
limited to IAP-gated requests (the review group) or bearer callers in the editor allow-list."""
import pytest
from fastapi import HTTPException

from ugs_warehouse import comments


class _IapReq:
    """An IAP-gated request — the header is only present when IAP authorized it."""
    def __init__(self, email):
        self.headers = {"x-goog-authenticated-user-email": f"accounts.google.com:{email}"}


class _BearerReq:
    """A hazards-review request: no IAP header; identity comes from a (here, pre-verified) bearer."""
    def __init__(self, email):
        self._email = email
        self.headers = {"authorization": "Bearer fake"}


@pytest.fixture(autouse=True)
def _stub_bearer(monkeypatch):
    # Bypass real Firebase verification: the bearer's email is whatever _BearerReq carries.
    monkeypatch.setattr(comments, "_verify_firebase_email", lambda _t: None)


def test_iap_request_is_always_allowed_to_write():
    # IAP already gated to the review group → editor.
    assert comments._require_editor(_IapReq("reviewer@utah.gov")) == "reviewer@utah.gov"


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


def test_unauthenticated_is_401(monkeypatch):
    monkeypatch.setattr(comments, "_verify_firebase_email", lambda _t: None)

    class _NoAuth:
        headers = {}
    with pytest.raises(HTTPException) as e:
        comments._require_editor(_NoAuth())
    assert e.value.status_code == 401
