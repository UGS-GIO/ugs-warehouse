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
    def __init__(self, name: str, blobs: dict[tuple[str, str], _FakeBlob]):
        self.name = name
        self._blobs = blobs

    def blob(self, key: str) -> _FakeBlob:
        # Keyed on (bucket, key), NOT key alone: source and destination are different buckets, and
        # a shared key-only namespace would alias them the moment a src key equals a dest path —
        # silently turning a cross-bucket assertion into a self-comparison that always passes.
        b = self._blobs.get((self.name, key))
        if b is None:
            b = _FakeBlob(self.name, key)
            self._blobs[(self.name, key)] = b
        return b


class _FakeClient:
    def __init__(self):
        self._blobs: dict[tuple[str, str], _FakeBlob] = {}

    def bucket(self, name: str) -> _FakeBucket:
        return _FakeBucket(name, self._blobs)

    def blob(self, bucket: str, key: str) -> _FakeBlob:
        return self._blobs[(bucket, key)]


@pytest.fixture(autouse=True)
def _reset_cached_client(monkeypatch):
    monkeypatch.setattr(gcs, "_cached_gcs_client", None)


def test_copy_from_uri_parses_bucket_and_key(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(gcs, "_gcs_client", lambda: fake)

    gcs.copy_from_uri("gs://stagedrasters/slope/foo.cog.tif", "cog/slope/foo.cog.tif",
                       content_type="image/tiff")

    dest = fake.blob(gcs.config.BUCKET, "cog/slope/foo.cog.tif")
    assert dest.bucket_name == gcs.config.BUCKET
    src = fake.blob("stagedrasters", "slope/foo.cog.tif")
    assert src.bucket_name == "stagedrasters"


def test_copy_from_uri_sets_metadata_before_rewrite(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(gcs, "_gcs_client", lambda: fake)

    gcs.copy_from_uri("gs://stagedrasters/x.tif", "cog/x.tif",
                       content_type="image/tiff", cache_control=gcs.CACHE_IMMUTABLE)

    dest = fake.blob(gcs.config.BUCKET, "cog/x.tif")
    assert dest.content_type == "image/tiff"
    assert dest.cache_control == gcs.CACHE_IMMUTABLE


def test_copy_from_uri_loops_until_token_none(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(gcs, "_gcs_client", lambda: fake)

    gcs.copy_from_uri("gs://stagedrasters/x.tif", "cog/x.tif", content_type="image/tiff")

    dest = fake.blob(gcs.config.BUCKET, "cog/x.tif")
    # First call token=None (start), second call token="next-token" (continuation), then stop.
    assert dest.rewrite_calls == [None, "next-token"]


def test_copy_from_uri_no_cache_control_leaves_default(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(gcs, "_gcs_client", lambda: fake)

    gcs.copy_from_uri("gs://stagedrasters/x.tif", "cog/x.tif", content_type="image/tiff")

    dest = fake.blob(gcs.config.BUCKET, "cog/x.tif")
    assert dest.cache_control is None


@pytest.mark.parametrize("bad_uri", ["s3://wrong-scheme/x", "gs://no-key-bucket-only", "gs://"])
def test_copy_from_uri_rejects_malformed_uri(bad_uri):
    with pytest.raises(ValueError):
        gcs.copy_from_uri(bad_uri, "dest.tif", content_type="image/tiff")


def test_copy_from_uri_raises_when_the_rewrite_stops_short(monkeypatch):
    """A short copy must fail loudly, not leave a truncated COG a STAC item points at.

    This is the worst failure available here: `promote()` reports success, the item publishes,
    and the object is silently incomplete.
    """
    fake = _FakeClient()
    monkeypatch.setattr(gcs, "_gcs_client", lambda: fake)
    dest = fake.bucket(gcs.config.BUCKET).blob("cog/x.tif")
    # GCS says "done" (token=None) having moved only part of the object.
    dest.rewrite = lambda source, token=None: (None, 120, 300)

    with pytest.raises(OSError, match="incomplete rewrite"):
        gcs.copy_from_uri("gs://stagedrasters/x.tif", "cog/x.tif", content_type="image/tiff")


def test_copy_from_uri_propagates_a_mid_loop_failure(monkeypatch):
    """An error partway through the continuation loop must surface, not be swallowed."""
    fake = _FakeClient()
    monkeypatch.setattr(gcs, "_gcs_client", lambda: fake)
    dest = fake.bucket(gcs.config.BUCKET).blob("cog/x.tif")
    calls: list[str | None] = []

    def flaky(source, token=None):
        calls.append(token)
        if token is None:
            return "next-token", 100, 300
        raise ConnectionError("GCS went away mid-rewrite")

    dest.rewrite = flaky

    with pytest.raises(ConnectionError):
        gcs.copy_from_uri("gs://stagedrasters/x.tif", "cog/x.tif", content_type="image/tiff")
    assert calls == [None, "next-token"]  # it did resume before failing
