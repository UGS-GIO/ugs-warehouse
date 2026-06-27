"""Restyle (manifest → STAC render rebind) — hermetic, in-memory GCS + fake styles manifest."""
from ugs_warehouse import restyle as R
from ugs_warehouse.core import gcs, stac, styles


def _mem_gcs(monkeypatch):
    store: dict[str, bytes] = {}
    monkeypatch.setattr(gcs, "put_bytes", lambda b, p, **k: store.__setitem__(p, b))
    monkeypatch.setattr(gcs, "get_bytes", lambda p: store[p])
    monkeypatch.setattr(gcs, "list_paths", lambda pre: [k for k in store if k.startswith(pre)])
    return store


def _fake_manifest(monkeypatch, entries):
    monkeypatch.setattr(styles, "_manifest", lambda: tuple(entries))
    monkeypatch.setattr(styles, "warm", lambda: len(entries))


def _props(store, coll_path, iid):
    import json
    return json.loads(store[stac.item_object_path(coll_path, iid)].decode()).get("properties", {})


def test_restyle_binds_matching_item_and_skips_others(monkeypatch):
    store = _mem_gcs(monkeypatch)
    _fake_manifest(monkeypatch, [
        {"itemId": "enmin_ucrc_wells", "render": "by-purpose", "kind": "vector",
         "assets": ["pmtiles"], "path": "styles/x/by-purpose.json", "title": "wells"},
    ])
    wells = stac.build_item(item_id="enmin_ucrc_wells", collection="ugs-serving-topics",
                            geometry=None, bbox=[-114, 37, -109, 42], datetime_iso="2026-01-01T00:00:00Z",
                            properties={"title": "Wells"},
                            assets={"pmtiles": {"href": "h", "type": "application/vnd.pmtiles"}})
    stac.write_item(wells)
    pub = stac.build_item(item_id="DS-8", collection="DS", collection_path="ugs-publications/DS",
                          geometry=None, bbox=None, datetime_iso=None, properties={"title": "x"},
                          assets={"publication": {"href": "p", "type": "application/pdf"}})
    stac.write_item(pub)

    n = R.restyle()

    assert n == 1
    assert "by-purpose" in _props(store, "ugs-serving-topics", "enmin_ucrc_wells")["ugs:renders"]
    # nested pub layout preserved + left untouched (no style match)
    assert stac.item_object_path("ugs-publications/DS", "DS-8") in store
    assert "ugs:renders" not in _props(store, "ugs-publications/DS", "DS-8")


def test_restyle_drops_a_removed_style(monkeypatch):
    store = _mem_gcs(monkeypatch)
    # item already carries a render binding...
    _fake_manifest(monkeypatch, [
        {"itemId": "lyr", "render": "default", "kind": "vector", "assets": ["pmtiles"],
         "path": "styles/lyr/default.json"},
    ])
    item = stac.build_item(item_id="lyr", collection="ugs-serving-topics", geometry=None,
                           bbox=[0, 1, 2, 3], datetime_iso=None, properties={"title": "L"},
                           assets={"pmtiles": {"href": "h", "type": "application/vnd.pmtiles"}})
    stac.write_item(item)
    R.restyle()
    assert "ugs:renders" in _props(store, "ugs-serving-topics", "lyr")

    # ...now the style is unpublished → manifest empty → rebind must drop renders.
    _fake_manifest(monkeypatch, [])
    R.restyle()
    p = _props(store, "ugs-serving-topics", "lyr")
    assert "ugs:renders" not in p
    import json
    doc = json.loads(store[stac.item_object_path("ugs-serving-topics", "lyr")].decode())
    assert "render" not in " ".join(doc.get("stac_extensions", []))  # no render-ext declaration
    assert "style" not in doc.get("assets", {})


def test_restyle_dry_run_writes_nothing(monkeypatch):
    store = _mem_gcs(monkeypatch)
    _fake_manifest(monkeypatch, [
        {"itemId": "lyr", "render": "default", "kind": "vector", "assets": ["pmtiles"],
         "path": "styles/lyr/default.json"},
    ])
    item = stac.build_item(item_id="lyr", collection="ugs-serving-topics", geometry=None,
                           bbox=[0, 1, 2, 3], datetime_iso=None, properties={"title": "L"},
                           assets={"pmtiles": {"href": "h", "type": "application/vnd.pmtiles"}})
    stac.write_item(item)
    before = dict(store)
    n = R.restyle(dry_run=True)
    assert n == 1                 # would bind one
    assert store == before        # but wrote nothing


def test_restyle_collection_all(monkeypatch):
    store = _mem_gcs(monkeypatch)
    _fake_manifest(monkeypatch, [
        {"itemId": "enmin_ucrc_wells", "render": "by-purpose", "kind": "vector",
         "assets": ["pmtiles"], "path": "styles/x/by-purpose.json", "title": "wells"},
        {"itemId": "DS-8", "render": "default", "kind": "vector", "assets": ["publication"],
         "path": "styles/ds8/default.json", "title": "ds8"},
    ])
    wells = stac.build_item(item_id="enmin_ucrc_wells", collection="ugs-serving-topics",
                            geometry=None, bbox=[-114, 37, -109, 42], datetime_iso="2026-01-01T00:00:00Z",
                            properties={"title": "Wells"},
                            assets={"pmtiles": {"href": "h", "type": "application/vnd.pmtiles"}})
    stac.write_item(wells)
    pub = stac.build_item(item_id="DS-8", collection="DS", collection_path="ugs-publications/DS",
                          geometry=None, bbox=None, datetime_iso=None, properties={"title": "x"},
                          assets={"publication": {"href": "p", "type": "application/pdf"}})
    stac.write_item(pub)

    n = R.restyle(collection="all")
    assert n == 2
    assert "by-purpose" in _props(store, "ugs-serving-topics", "enmin_ucrc_wells")["ugs:renders"]
    assert "default" in _props(store, "ugs-publications/DS", "DS-8")["ugs:renders"]


def test_restyle_collection_custom_prefix(monkeypatch):
    store = _mem_gcs(monkeypatch)
    _fake_manifest(monkeypatch, [
        {"itemId": "enmin_ucrc_wells", "render": "by-purpose", "kind": "vector",
         "assets": ["pmtiles"], "path": "styles/x/by-purpose.json", "title": "wells"},
        {"itemId": "DS-8", "render": "default", "kind": "vector", "assets": ["publication"],
         "path": "styles/ds8/default.json", "title": "ds8"},
    ])
    wells = stac.build_item(item_id="enmin_ucrc_wells", collection="ugs-serving-topics",
                            geometry=None, bbox=[-114, 37, -109, 42], datetime_iso="2026-01-01T00:00:00Z",
                            properties={"title": "Wells"},
                            assets={"pmtiles": {"href": "h", "type": "application/vnd.pmtiles"}})
    stac.write_item(wells)
    pub = stac.build_item(item_id="DS-8", collection="DS", collection_path="ugs-publications/DS",
                          geometry=None, bbox=None, datetime_iso=None, properties={"title": "x"},
                          assets={"publication": {"href": "p", "type": "application/pdf"}})
    stac.write_item(pub)

    n = R.restyle(collection="ugs-publications")
    assert n == 1
    assert "default" in _props(store, "ugs-publications/DS", "DS-8")["ugs:renders"]
    assert "ugs:renders" not in _props(store, "ugs-serving-topics", "enmin_ucrc_wells")


def test_report_flags_asset_miss_and_orphans(monkeypatch):
    store = _mem_gcs(monkeypatch)
    _fake_manifest(monkeypatch, [
        # matches an item that has the pmtiles asset -> styled
        {"itemId": "good_layer", "render": "default", "kind": "vector",
         "assets": ["pmtiles"], "path": "styles/good/default.json"},
        # id matches an item, but the item lacks the targeted asset key -> ASSET-MISS
        {"itemId": "wrong_asset", "render": "default", "kind": "vector",
         "assets": ["pmtiles"], "path": "styles/wa/default.json"},
        # no item with this id anywhere -> ORPHAN (the classic 'mistranslation')
        {"itemId": "ghost_layer", "render": "default", "kind": "vector",
         "assets": ["pmtiles"], "path": "styles/ghost/default.json"},
    ])
    stac.write_item(stac.build_item(
        item_id="good_layer", collection="ugs-serving-topics", geometry=None,
        bbox=[0, 1, 2, 3], datetime_iso=None, properties={"title": "g"},
        assets={"pmtiles": {"href": "h", "type": "application/vnd.pmtiles"}}))
    stac.write_item(stac.build_item(
        item_id="wrong_asset", collection="ugs-serving-topics", geometry=None,
        bbox=[0, 1, 2, 3], datetime_iso=None, properties={"title": "w"},
        assets={"tiles": {"href": "h", "type": "application/vnd.pmtiles"}}))  # wrong key!

    before = dict(store)
    rc = R.report(collection="ugs-serving-topics")
    # one asset-miss + one orphan
    assert rc == 2
    assert store == before  # report writes nothing
