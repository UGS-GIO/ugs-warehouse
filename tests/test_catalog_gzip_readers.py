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


# The magic bytes are a guess: arbitrary content can start 1f 8b. A body that merely looks gzipped
# must fall through as raw rather than raise out of what is documented as a plain download.
def test_get_reads_a_body_that_only_looks_gzipped(monkeypatch, _no_deadline):
    body = b"\x1f\x8b" + json.dumps(DOC).encode()[2:]
    monkeypatch.setattr(gen_db.urllib.request, "urlopen", lambda *a, **k: _Resp(body))
    # The point is that the gunzip attempt does not escape: the raw bytes reach the decode, which
    # fails on its own terms rather than as a BadGzipFile out of a plain download.
    with pytest.raises(UnicodeDecodeError):
        gen_db._get("https://cdn/catalog.json")


def test_gcs_gunzip_falls_back_on_a_false_positive():
    from ugs_warehouse.core import gcs

    assert gcs._gunzip(b"\x1f\x8bnot actually gzip") == b"\x1f\x8bnot actually gzip"
    assert gcs._gunzip(b"plain") == b"plain"
    assert gcs._gunzip(gzip.compress(b"real")) == b"real"
