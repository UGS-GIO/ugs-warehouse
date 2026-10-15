"""Ops-console metadata overrides for publications: a migrated series is patched on its
collection.json, an old-layout one on its item, and ingest applies the override to the Collection."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from ugs_warehouse.core import config, gcs, stac
from ugs_warehouse.pubs import ingest, sink_stac, source

P = {"series_id": "OFR-1", "pub_name": "Source title", "pub_year": "2026",
     "pub_publisher": "Utah Geological Survey", "full_citation": "Source citation."}
COLL_PATH = "ugs-publications/OFR/OFR-1"
COLL_OBJ = stac.collection_object_path(COLL_PATH)
ITEM_OBJ = stac.item_object_path("ugs-publications/OFR", "OFR-1")
SERIES_CATALOG = f"{config.STAC_PREFIX}/ugs-publications/OFR/catalog.json"


@pytest.fixture
def ov(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "admin" / "ops" / "overrides.py"
    spec = importlib.util.spec_from_file_location("ops_overrides_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def store(monkeypatch):
    objs: dict[str, bytes] = {}

    def get(p):
        if p not in objs:
            raise FileNotFoundError(p)
        return objs[p]

    def put_if_unchanged(b, p, version, **k):
        if objs.get(p) != version["bytes"]:
            raise gcs.Changed(p)
        objs[p] = b

    monkeypatch.setattr(gcs, "put_bytes", lambda b, p, **k: objs.__setitem__(p, b))
    monkeypatch.setattr(gcs, "get_bytes", get)
    monkeypatch.setattr(gcs, "get_bytes_versioned", lambda p: (get(p), {"bytes": get(p)}))
    monkeypatch.setattr(gcs, "put_bytes_if_unchanged", put_if_unchanged)
    monkeypatch.setattr(gcs, "exists", lambda p: p in objs)
    monkeypatch.setattr(gcs, "delete", lambda p: objs.pop(p, None))
    monkeypatch.setattr(gcs, "list_paths", lambda pre: [k for k in objs if k.startswith(pre)])
    monkeypatch.setattr(source, "read_pubs", lambda: [P])
    return objs


def _put(store, path, doc):
    store[path] = json.dumps(doc).encode()


def _get(store, path):
    return json.loads(store[path])


def test_migrated_pub_override_patches_the_collection(ov, store):
    _put(store, COLL_OBJ, {"id": "OFR-1", "title": "Source title", "description": "Source citation."})
    _put(store, SERIES_CATALOG, {"id": "OFR"})

    res = ov.save("OFR-1", description="Hand text", title="Hand title")

    assert res["applied"] is True
    assert _get(store, COLL_OBJ) == {"id": "OFR-1", "title": "Hand title", "description": "Hand text"}
    assert _get(store, stac.override_object("OFR-1"))["description"] == "Hand text"


def test_pending_series_pub_with_a_collection_is_patched_on_the_item_the_catalog_serves(ov, store):
    _put(store, COLL_OBJ, {"id": "OFR-1", "title": "T", "description": "D"})
    _put(store, ITEM_OBJ, {"id": "OFR-1", "properties": {"title": "old"}})

    assert ov.save("ofr-1", description="Hand text")["applied"] is True

    assert _get(store, ITEM_OBJ)["properties"]["description"] == "Hand text"
    assert "Hand text" not in json.dumps(_get(store, COLL_OBJ))


def test_pending_series_pub_with_only_a_collection_is_patched_on_the_collection(ov, store):
    _put(store, COLL_OBJ, {"id": "OFR-1", "title": "T", "description": "D"})

    assert ov.save("ofr-1", description="Hand text")["applied"] is True

    assert _get(store, COLL_OBJ)["description"] == "Hand text"


def test_migrated_series_pub_without_a_collection_saves_the_sidecar_only(ov, store):
    _put(store, SERIES_CATALOG, {"id": "OFR"})
    _put(store, ITEM_OBJ, {"id": "OFR-1", "properties": {"title": "old"}})

    res = ov.save("OFR-1", description="Hand text")

    assert res["applied"] is False
    assert "description" not in _get(store, ITEM_OBJ)["properties"]
    assert stac.override_object("OFR-1") in store


def test_old_layout_pub_override_patches_the_item(ov, store):
    _put(store, ITEM_OBJ, {"id": "OFR-1", "properties": {"title": "old"}})

    res = ov.save("OFR-1", description="Hand text", title="Hand title")

    assert res["applied"] is True
    assert _get(store, ITEM_OBJ)["properties"] == {"title": "Hand title", "description": "Hand text"}


def test_unpublished_pub_saves_the_sidecar_only(ov, store):
    res = ov.save("OFR-1", description="Hand text")
    assert res["applied"] is False
    assert stac.override_object("OFR-1") in store


def test_ingest_applies_the_override_to_the_collection(store):
    _put(store, stac.override_object("OFR-1"), {"source": "manual", "description": "Hand text",
                                                 "title": "Hand title"})
    overrides = ingest._overrides_by_sid()

    coll, _ = sink_stac.build_publication(P, [], override=overrides.get("OFR-1"))

    assert coll["title"] == "Hand title" and coll["description"] == "Hand text"


def test_an_unreadable_override_fails_the_ingest(store):
    store[stac.override_object("OFR-1")] = b"{not json"
    with pytest.raises(ValueError, match="unreadable override .*OFR-1.json: JSONDecodeError"):
        ingest._overrides_by_sid()


def test_an_override_that_is_not_an_object_fails_the_ingest(store):
    store[stac.override_object("OFR-1")] = b'["Hand text"]'
    with pytest.raises(ValueError, match="override .*OFR-1.json is not a JSON object"):
        ingest._overrides_by_sid()


def test_a_collection_patch_rerenders_its_readme(store):
    _put(store, COLL_OBJ, {"id": "OFR-1", "title": "Source title", "description": "Source citation.",
                           "links": [{"rel": "item", "href": "./a/a.json"}]})

    assert stac.patch_published(COLL_OBJ, {"title": "Hand title"}, target="fields") is True

    readme = store[f"{config.STAC_PREFIX}/{COLL_PATH}/README.md"].decode()
    assert readme.startswith("# Hand title")


def test_a_collection_patch_retries_once_when_the_collection_changes_under_it(store, monkeypatch):
    _put(store, COLL_OBJ, {"id": "OFR-1", "title": "T", "description": "D"})
    real = gcs.put_bytes_if_unchanged
    calls = []

    def racing(b, p, version, **k):
        calls.append(p)
        if len(calls) == 1:  # someone else writes between this read and write
            _put(store, COLL_OBJ, {"id": "OFR-1", "title": "T", "description": "D", "keywords": ["x"]})
        real(b, p, version, **k)

    monkeypatch.setattr(gcs, "put_bytes_if_unchanged", racing)
    assert stac.patch_published(COLL_OBJ, {"title": "Hand title"}, target="fields") is True
    assert _get(store, COLL_OBJ) == {"id": "OFR-1", "title": "Hand title", "description": "D",
                                     "keywords": ["x"]}

    def always_changed(b, p, version, **k):
        raise gcs.Changed(p)

    monkeypatch.setattr(gcs, "put_bytes_if_unchanged", always_changed)
    with pytest.raises(gcs.Changed):
        stac.patch_published(COLL_OBJ, {"title": "Other"}, target="fields")


def test_an_item_patch_merges_into_properties_and_keeps_the_rest(store):
    _put(store, ITEM_OBJ, {"id": "OFR-1", "properties": {"title": "old", "ugs:scale": "24k"}, "links": []})

    assert stac.patch_published(ITEM_OBJ, {"title": "new"}) is True

    assert _get(store, ITEM_OBJ) == {"id": "OFR-1", "properties": {"title": "new", "ugs:scale": "24k"},
                                     "links": []}


def test_a_patch_of_something_unpublished_returns_false(store):
    assert stac.patch_published(ITEM_OBJ, {"title": "new"}) is False
    assert stac.patch_published(COLL_OBJ, {"title": "new"}, target="fields") is False


def test_an_item_patch_is_conditional_and_raises_when_the_item_keeps_changing(store, monkeypatch):
    _put(store, ITEM_OBJ, {"id": "OFR-1", "properties": {"title": "old"}})
    seen = []

    def always_changed(b, p, version, **k):
        seen.append(k["content_type"])
        raise gcs.Changed(p)

    monkeypatch.setattr(gcs, "put_bytes_if_unchanged", always_changed)
    with pytest.raises(gcs.Changed):
        stac.patch_published(ITEM_OBJ, {"title": "new"})
    assert seen == ["application/geo+json"] * 2


def test_a_read_error_other_than_missing_raises(store, monkeypatch):
    def boom(p):
        raise PermissionError(p)

    monkeypatch.setattr(gcs, "get_bytes_versioned", boom)
    with pytest.raises(PermissionError):
        stac.patch_published(ITEM_OBJ, {"title": "new"})


def test_an_unknown_patch_target_is_refused(store):
    with pytest.raises(ValueError, match="unknown patch target"):
        stac.patch_published(ITEM_OBJ, {"title": "new"}, target="links")


def test_save_keeps_the_sidecar_and_reports_a_live_patch_that_failed(ov, store, monkeypatch):
    _put(store, ITEM_OBJ, {"id": "OFR-1", "properties": {"title": "old"}})

    def always_changed(b, p, version, **k):
        raise gcs.Changed(p)

    monkeypatch.setattr(gcs, "put_bytes_if_unchanged", always_changed)
    res = ov.save("OFR-1", description="Hand text")

    assert res["applied"] is False and res["apply_error"].startswith("Changed")
    assert _get(store, stac.override_object("OFR-1"))["description"] == "Hand text"
