"""core/gcs.py — copy_from_uri's server-side rewrite (ugs-ingest#183: the prior in-memory
buffer OOM-killed ugs-warehouse-service on the first live raster promote). Mocks the
google-cloud-storage Client entirely — no real network/auth, just verifying the call shape:
bucket/key parsed off the gs:// URI, metadata set before rewrite, and the continuation-token
loop actually loops until GCS signals done (token=None).
"""
from __future__ import annotations

import pytest

from ugs_warehouse.core import gcs


class _FakeBlob:
    def __init__(self, bucket_name: str, key: str):
        self.bucket_name = bucket_name
        self.key = key
        self.content_type: str | None = None
        self.cache_control: str | None = None
        self.rewrite_calls: list[str | None] = []

    def rewrite(self, source, token=None):
        self.rewrite_calls.append(token)
        # Simulate GCS needing two continuation steps before it's done.
        if len(self.rewrite_calls) < 2:
            return "next-token", 100, 300
        return None, 300, 300


class _FakeBucket:
    def __init__(self, name: str, blobs: dict[str, _FakeBlob]):
        self.name = name
        self._blobs = blobs

    def blob(self, key: str) -> _FakeBlob:
        b = self._blobs.get(key)
        if b is None:
            b = _FakeBlob(self.name, key)
            self._blobs[key] = b
        return b


class _FakeClient:
    def __init__(self):
        self._blobs: dict[str, _FakeBlob] = {}

    def bucket(self, name: str) -> _FakeBucket:
        return _FakeBucket(name, self._blobs)


@pytest.fixture(autouse=True)
def _reset_cached_client(monkeypatch):
    monkeypatch.setattr(gcs, "_cached_gcs_client", None)


def test_copy_from_uri_parses_bucket_and_key(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(gcs, "_gcs_client", lambda: fake)

    gcs.copy_from_uri("gs://stagedrasters/slope/foo.cog.tif", "cog/slope/foo.cog.tif",
                       content_type="image/tiff")

    dest = fake._blobs["cog/slope/foo.cog.tif"]
    assert dest.bucket_name == gcs.config.BUCKET
    src = fake._blobs["slope/foo.cog.tif"]
    assert src.bucket_name == "stagedrasters"


def test_copy_from_uri_sets_metadata_before_rewrite(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(gcs, "_gcs_client", lambda: fake)

    gcs.copy_from_uri("gs://stagedrasters/x.tif", "cog/x.tif",
                       content_type="image/tiff", cache_control=gcs.CACHE_IMMUTABLE)

    dest = fake._blobs["cog/x.tif"]
    assert dest.content_type == "image/tiff"
    assert dest.cache_control == gcs.CACHE_IMMUTABLE


def test_copy_from_uri_loops_until_token_none(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(gcs, "_gcs_client", lambda: fake)

    gcs.copy_from_uri("gs://stagedrasters/x.tif", "cog/x.tif", content_type="image/tiff")

    dest = fake._blobs["cog/x.tif"]
    # First call token=None (start), second call token="next-token" (continuation), then stop.
    assert dest.rewrite_calls == [None, "next-token"]


def test_copy_from_uri_no_cache_control_leaves_default(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(gcs, "_gcs_client", lambda: fake)

    gcs.copy_from_uri("gs://stagedrasters/x.tif", "cog/x.tif", content_type="image/tiff")

    dest = fake._blobs["cog/x.tif"]
    assert dest.cache_control is None


@pytest.mark.parametrize("bad_uri", ["s3://wrong-scheme/x", "gs://no-key-bucket-only", "gs://"])
def test_copy_from_uri_rejects_malformed_uri(bad_uri):
    with pytest.raises(ValueError):
        gcs.copy_from_uri(bad_uri, "dest.tif", content_type="image/tiff")
