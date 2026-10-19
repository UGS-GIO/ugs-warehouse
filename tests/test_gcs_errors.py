"""A missing object is an expected state; GCS being unreachable is a failure and must surface."""
from __future__ import annotations

import pytest

from ugs_warehouse.core import gcs, item_mirror
from ugs_warehouse.vector import fingerprint
from ugs_warehouse.vector.topics import Topic

TOPIC = Topic(schema="hazards", layer="hazards_qfaults_current")
ITEM = {"type": "Feature", "stac_version": "1.1.0", "id": "a", "collection": "c",
        "geometry": {"type": "Point", "coordinates": [-112.0, 40.0]},
        "bbox": [-112.0, 40.0, -112.0, 40.0], "properties": {"datetime": "2026-01-01T00:00:00Z"},
        "links": [], "assets": {}}


def _unreachable(*_a, **_k):
    raise ConnectionError("GCS unreachable")


def test_a_topic_with_no_published_item_rebuilds():
    assert fingerprint.published_hash(TOPIC) is None


def test_an_unreachable_bucket_does_not_pass_for_a_missing_item(monkeypatch):
    monkeypatch.setattr(gcs, "get_bytes", _unreachable)
    with pytest.raises(ConnectionError):
        fingerprint.published_hash(TOPIC)


def test_a_failed_mirror_upload_raises(monkeypatch):
    monkeypatch.setattr(gcs, "upload", _unreachable)
    with pytest.raises(ConnectionError):
        item_mirror.write("ugs-serving-topics/hazards", [ITEM])


def _fake_obs_delete(monkeypatch, missing=(), broken=()):
    """obs.delete over a set of live paths: a batch deletes what it can, then raises its first
    error, as obstore reports one error per call."""
    live = {f"p/{i}" for i in range(5)} - set(missing)
    calls: list = []

    def delete(store, paths):
        batch = [paths] if isinstance(paths, str) else list(paths)
        calls.append(batch)
        first = None
        for p in batch:
            if p in broken:
                first = first or PermissionError(f"403 {p}")
            elif p not in live:
                first = first or FileNotFoundError(p)
            else:
                live.discard(p)
        if first:
            raise first

    monkeypatch.setattr(gcs, "_store", lambda bucket=None: None)
    monkeypatch.setattr(gcs.obs, "delete", delete)
    return live, calls


def test_delete_many_sends_batched_calls_and_ignores_missing_objects(monkeypatch):
    live, calls = _fake_obs_delete(monkeypatch, missing={"p/1"})
    monkeypatch.setattr(gcs, "DELETE_BATCH", 3)
    gcs.delete_many([f"p/{i}" for i in range(5)])
    assert live == set()
    assert calls[0] == ["p/0", "p/1", "p/2"] and ["p/3", "p/4"] in calls


def test_delete_many_raises_on_a_real_failure_even_behind_a_missing_object(monkeypatch):
    # The batch's first error is the not-found; the 403 behind it must still surface.
    live, _ = _fake_obs_delete(monkeypatch, missing={"p/0"}, broken={"p/3"})
    with pytest.raises(RuntimeError, match=r"1 object\(s\) not deleted: \['p/3: PermissionError"):
        gcs.delete_many([f"p/{i}" for i in range(5)])
    assert live == {"p/3"}


def test_delete_many_not_strict_ignores_failures_like_delete(monkeypatch):
    live, _ = _fake_obs_delete(monkeypatch, broken={"p/2"})
    gcs.delete_many([f"p/{i}" for i in range(5)], strict=False)
    assert live == {"p/2"}
