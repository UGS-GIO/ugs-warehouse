"""file:size + file:checksum — what a write reports, and how the item carries it (#215).

Hermetic: obstore is stubbed, so the writes hash real bytes without touching GCS.
"""
from __future__ import annotations

import hashlib
import json

from ugs_warehouse.core import gcs, stac


def _stub_store(monkeypatch) -> list[tuple]:
    """Record obstore puts instead of performing them."""
    puts: list[tuple] = []
    monkeypatch.setattr(gcs, "_store", lambda: object())
    monkeypatch.setattr(gcs.obs, "put", lambda store, path, body, attributes=None: puts.append((path, body)))
    return puts


def test_put_bytes_reports_size_and_multihash_sha256(monkeypatch):
    _stub_store(monkeypatch)
    data = b"quaternary faults"

    meta = gcs.put_bytes(data, "x/y.json", content_type="application/json")

    assert meta.size == len(data)
    # multihash, not a bare digest: 0x12 = sha2-256, 0x20 = 32 bytes, then the digest.
    assert meta.checksum == "1220" + hashlib.sha256(data).hexdigest()


def test_upload_hashes_the_file_it_uploaded(monkeypatch, tmp_path):
    _stub_store(monkeypatch)
    local = tmp_path / "topic.parquet"
    local.write_bytes(b"PAR1" * 1000)

    meta = gcs.upload(str(local), "archive/topic.parquet", content_type=gcs.config.PARQUET_MIME)

    assert meta == gcs.FileMeta(4000, "1220" + hashlib.sha256(b"PAR1" * 1000).hexdigest())


def test_upload_streams_past_the_hasher(monkeypatch, tmp_path):
    """Bigger than one hash chunk — the digest must not depend on the file fitting in one read."""
    _stub_store(monkeypatch)
    blob = b"\xa5" * (gcs._HASH_CHUNK * 2 + 7)
    local = tmp_path / "big.tif"
    local.write_bytes(blob)

    meta = gcs.upload(str(local), "cogs/big.tif", content_type=gcs.config.COG_MIME)

    assert meta.size == len(blob)
    assert meta.checksum == "1220" + hashlib.sha256(blob).hexdigest()


def test_file_fields_omits_a_checksum_nobody_computed():
    # A server-side copy knows the size and nothing else — an absent value beats a fabricated one.
    assert stac.file_fields(gcs.FileMeta(512)) == {"file:size": 512}
    assert stac.file_fields(gcs.FileMeta(512, "1220ab")) == {"file:size": 512, "file:checksum": "1220ab"}
    assert stac.file_fields(None) == {}


def test_build_item_declares_the_file_extension_only_when_an_asset_carries_fields():
    def item(assets: dict) -> dict:
        return stac.build_item(item_id="t", collection="c", geometry=None, bbox=None,
                               datetime_iso="2026-01-01T00:00:00Z", properties={}, assets=assets)

    bare = item({"data": {"href": "https://example.org/t.parquet", "roles": ["data"]}})
    assert stac.FILE_EXT not in bare.get("stac_extensions", [])

    stamped = item({"data": {"href": "https://example.org/t.parquet", "roles": ["data"],
                             "file:size": 9}})
    assert stac.FILE_EXT in stamped["stac_extensions"]


def test_prior_file_fields_reads_only_the_file_keys(monkeypatch):
    published = {"assets": {
        "data": {"href": "https://example.org/t.parquet", "file:size": 12, "file:checksum": "1220aa",
                 "table:columns": [{"name": "id"}]},
        "pmtiles": {"href": "https://example.org/t.pmtiles"},   # never stamped → not carried
    }}
    monkeypatch.setattr(stac.gcs, "get_bytes", lambda p: json.dumps(published).encode())

    assert stac.prior_file_fields("ugs-serving-topics/hazards", "t") == {
        "data": {"file:size": 12, "file:checksum": "1220aa"}}


def test_prior_file_fields_is_empty_before_the_first_publish(monkeypatch):
    def missing(path):
        raise FileNotFoundError(path)

    monkeypatch.setattr(stac.gcs, "get_bytes", missing)
    assert stac.prior_file_fields("ugs-serving-topics/hazards", "t") == {}


def test_sink_stac_stamps_this_runs_writes_and_carries_the_rest(monkeypatch):
    """The data sinks report what they wrote; a skipped one keeps the published values."""
    from ugs_warehouse.vector import sink_stac
    from ugs_warehouse.vector.topics import Topic

    captured: dict = {}
    monkeypatch.setattr(sink_stac, "_bbox", lambda c, v: [0, 1, 2, 3])
    monkeypatch.setattr(sink_stac, "_row_count", lambda c, v: 5)
    monkeypatch.setattr(sink_stac, "_table_columns", lambda c, v: [])
    monkeypatch.setattr(sink_stac.stac, "manual_override", lambda iid: {})
    monkeypatch.setattr(sink_stac.stac, "prior_property", lambda cp, iid, prop: None)
    monkeypatch.setattr(sink_stac.gcs, "exists", lambda p: True)   # a thumbnail is published
    monkeypatch.setattr(sink_stac.stac, "prior_file_fields", lambda cp, iid: {
        "data": {"file:size": 1, "file:checksum": "1220stale"},
        "pmtiles": {"file:size": 2},
        "thumbnail": {"file:size": 3, "file:checksum": "1220thumb"},
    })
    monkeypatch.setattr(sink_stac.stac, "build_item",
                        lambda **k: captured.update(k) or {"assets": k["assets"]})
    for name in ("attach_renders", "attach_classification", "attach_iso"):
        monkeypatch.setattr(sink_stac.stac, name, lambda i: None)
    monkeypatch.setattr(sink_stac.stac, "write_item", lambda i: "stac/path.json")

    sink_stac.write(Topic(schema="hazards", layer="hazards_qfaults_current"), None, "v",
                    file_meta={"data": gcs.FileMeta(4096, "1220fresh")})

    assets = captured["assets"]
    # The archive sink ran this time, so its values replace the published ones.
    assert assets["data"]["file:size"] == 4096
    assert assets["data"]["file:checksum"] == "1220fresh"
    # PMTiles and the thumbnail were not written by this run — the published values stand.
    assert assets["pmtiles"]["file:size"] == 2
    assert "file:checksum" not in assets["pmtiles"]
    assert assets["thumbnail"]["file:checksum"] == "1220thumb"
