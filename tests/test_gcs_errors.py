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
