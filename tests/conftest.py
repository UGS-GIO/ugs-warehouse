import json
import time
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from google.auth import jwt
from google.auth.crypt import es256


@pytest.fixture(autouse=True)
def _in_memory_gcs(monkeypatch):
    """Back every bucket with an obstore MemoryStore, so code that reaches GCS without a stub runs
    against an empty in-memory bucket instead of the network. A test that stubs a gcs call still
    overrides this."""
    from obstore.store import MemoryStore

    from ugs_warehouse.core import gcs

    def _no_client(*_a, **_k):
        raise RuntimeError("tests must not reach GCS; the google-cloud-storage fallback is not stubbed")

    monkeypatch.setattr(gcs, "_cached_stores", {})
    monkeypatch.setattr(gcs, "GCSStore", lambda bucket=None, **_k: MemoryStore())
    monkeypatch.setattr(gcs, "_gcs_client", _no_client)
    # MemoryStore keeps no custom metadata, so the listing builders stamp from is empty by default.
    monkeypatch.setattr(gcs, "list_file_meta", lambda _prefix: {})


class FakeIap:
    """Mints ES256 tokens shaped like IAP's and serves the matching public key, so the verifiers run
    their real signature/audience/issuer checks against a key the test controls."""

    audience = "/projects/123/locations/us-central1/services/test-service"

    def __init__(self):
        key = ec.generate_private_key(ec.SECP256R1())
        pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
        self._signer = es256.ES256Signer.from_string(pem, key_id="test-kid")
        self.certs = {"test-kid": key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()}

    def token(self, email, aud=None, iss="https://cloud.google.com/iap", ttl=600):
        now = int(time.time())
        claims = {"aud": aud or self.audience, "iss": iss, "email": email,
                  "sub": "accounts.google.com:1", "iat": now, "exp": now + ttl}
        return jwt.encode(self._signer, claims).decode()

    def install(self, monkeypatch, module):
        body = json.dumps(self.certs).encode()
        fetch = lambda url, method="GET", **kw: SimpleNamespace(status=200, headers={}, data=body)  # noqa: E731
        monkeypatch.setattr(module, "cached_request", fetch)
        monkeypatch.setattr(module, "_audience", lambda: self.audience)


@pytest.fixture()
def fake_iap():
    return FakeIap()


@pytest.fixture()
def other_iap():
    """A second signer whose key the verifiers were never given: its tokens must fail."""
    return FakeIap()


# Tokens every IAP verifier must refuse, as FakeIap.token() overrides.
REJECTED_TOKENS = {
    "other-service": {"aud": "/projects/123/locations/us-central1/services/other-service"},
    "wrong-issuer": {"iss": "https://accounts.google.com"},
    "expired": {"ttl": -120},
}


@pytest.fixture(params=sorted(REJECTED_TOKENS))
def rejected_token_kwargs(request):
    return REJECTED_TOKENS[request.param]
