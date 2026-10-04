"""The catalog is stored gzipped, so the consumers that read it over HTTP must accept both forms.

GCS transcodes a `Content-Encoding: gzip` object back to plain bytes for a client that does not
advertise gzip, and `urllib` does not. But the object carries no `Vary: Accept-Encoding`, so a CDN
that cached the compressed representation serves those bytes to everyone. A reader that assumes
plain text raises `UnicodeDecodeError` and silently falls back to stale data.
"""
from __future__ import annotations

import gzip


def test_gcs_gunzip_falls_back_on_a_false_positive():
    from ugs_warehouse.core import gcs

    assert gcs._gunzip(b"\x1f\x8bnot actually gzip") == b"\x1f\x8bnot actually gzip"
    assert gcs._gunzip(b"plain") == b"plain"
    assert gcs._gunzip(gzip.compress(b"real")) == b"real"
