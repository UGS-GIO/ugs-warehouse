"""The catalog is stored gzipped, so the consumers that read it over HTTP must accept both forms.

GCS transcodes a `Content-Encoding: gzip` object back to plain bytes for a client that does not
advertise gzip, and `urllib` does not. But the object carries no `Vary: Accept-Encoding`, so a CDN
that cached the compressed representation serves those bytes to everyone. A reader that assumes
plain text raises `UnicodeDecodeError` and silently falls back to stale data.
"""
from __future__ import annotations

import gzip
import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "featureserv"))
import gen_db  # noqa: E402

DOC = {"collections": [{"id": "ugs-serving-topics"}]}


class _Resp(io.BytesIO):
    """Minimal urlopen stand-in: a context manager whose read() returns the body."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def _no_deadline(monkeypatch):
    monkeypatch.setattr(gen_db, "_remaining", lambda _d: 1)


@pytest.mark.parametrize(
    ("label", "body"),
    [("plain", json.dumps(DOC).encode()), ("gzipped", gzip.compress(json.dumps(DOC).encode()))],
)
def test_get_reads_either_representation(monkeypatch, _no_deadline, label, body):
    monkeypatch.setattr(gen_db.urllib.request, "urlopen", lambda *a, **k: _Resp(body))
    assert gen_db._get("https://cdn/catalog.json") == DOC, label


def test_body_leaves_plain_bytes_untouched():
    assert gen_db._body(_Resp(b'{"a": 1}')) == b'{"a": 1}'
