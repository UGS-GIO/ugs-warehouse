"""HEAD on a review-bucket object answers like GCS does behind the public CDN.

The viewer runs the same readers against the CDN (prod) and against this service (review). hyparquet
sends HEAD for the file size before it opens a GeoParquet, and treats anything but a 2xx (or a 403)
as a failed read, so a 405 here broke the review data table on every layer. The offline code reads
Content-Length, ETag and Last-Modified off HEAD too.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

serve = pytest.importorskip("ugs_warehouse.serve")
from fastapi.testclient import TestClient  # noqa: E402

PATH = "review/geoparquet/x/x.parquet"
BODY = bytes(range(256)) * 4
META = {"path": PATH, "size": len(BODY), "e_tag": '"abc123"', "version": None,
        "last_modified": datetime(2026, 9, 24, 17, 33, 20, tzinfo=timezone.utc)}


@pytest.fixture
def client(monkeypatch):
    reads: list[str] = []

    def head(_store, path):
        if path == serve.VIEWER_INDEX:
            return {**META, "path": path, "size": 512}
        if path != PATH:
            raise FileNotFoundError(path)
        return META

    def get_range(_store, path, start, end):
        reads.append("range")
        return BODY[start:end]

    def get(_store, path):
        reads.append("full")
        raise AssertionError("full read not expected in these tests")

    monkeypatch.setattr(serve, "obs", SimpleNamespace(head=head, get_range=get_range, get=get))
    c = TestClient(serve.app)
    c.reads = reads
    return c


def test_head_reports_the_object_without_reading_it(client):
    r = client.head(f"/{PATH}")
    assert r.status_code == 200
    assert r.headers["content-length"] == str(len(BODY))
    assert r.headers["accept-ranges"] == "bytes"
    assert r.headers["etag"] == '"abc123"'
    assert r.headers["last-modified"] == "Thu, 24 Sep 2026 17:33:20 GMT"
    assert r.headers["cache-control"] == "private, no-cache"
    assert r.content == b""
    assert client.reads == []


def test_head_ignores_range(client):
    r = client.head(f"/{PATH}", headers={"Range": "bytes=0-9"})
    assert r.status_code == 200
    assert r.headers["content-length"] == str(len(BODY))
    assert "content-range" not in r.headers


def test_a_range_get_carries_the_same_version_headers(client):
    r = client.get(f"/{PATH}", headers={"Range": "bytes=10-19"})
    assert r.status_code == 206
    assert r.content == BODY[10:20]
    assert r.headers["content-range"] == f"bytes 10-19/{len(BODY)}"
    assert r.headers["etag"] == '"abc123"'
    assert r.headers["last-modified"] == "Thu, 24 Sep 2026 17:33:20 GMT"
    # A validator alone would let the browser cache heuristically, and review files are rewritten in place.
    assert r.headers["cache-control"] == "private, no-cache"


def test_head_on_an_app_route_describes_the_viewer_shell(client):
    r = client.head(f"/{serve.VIEWER_PREFIX}/map")
    assert r.status_code == 200
    assert r.headers["content-length"] == "512"
    assert r.headers["content-type"].startswith("text/html")


def test_head_on_a_missing_file_is_404(client):
    assert client.head("/review/geoparquet/nope/nope.parquet").status_code == 404
