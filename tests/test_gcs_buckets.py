from __future__ import annotations

from ugs_warehouse.core import config, gcs


def test_store_is_cached_per_bucket(monkeypatch):
    made = []
    monkeypatch.setattr(gcs, "GCSStore", lambda bucket: made.append(bucket) or f"store:{bucket}")
    monkeypatch.setattr(gcs, "_cached_stores", {})
    monkeypatch.setattr(config, "BUCKET", "out-bucket")
    assert gcs._store() == "store:out-bucket"
    assert gcs._store("src-bucket") == "store:src-bucket"
    assert gcs._store() == "store:out-bucket" and gcs._store("src-bucket") == "store:src-bucket"
    assert made == ["out-bucket", "src-bucket"]


def test_list_paths_reads_the_named_bucket(monkeypatch):
    seen = []
    monkeypatch.setattr(gcs, "_store", lambda bucket=None: seen.append(bucket) or "s")
    monkeypatch.setattr(gcs.obs, "list", lambda store, prefix: [[{"path": f"{prefix}/a"}]])
    assert gcs.list_paths("p", bucket="src-bucket") == ["p/a"]
    assert gcs.list_paths("p") == ["p/a"]
    assert seen == ["src-bucket", None]
