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


# ---- post-write verification ------------------------------------------------------------
# A rebind that binds a stale manifest used to write it to every item and still exit 0, so the
# publish workflow went green while the catalog served old colors. _verify re-reads what was
# written and diffs it against the manifest, turning that silent failure into a loud one.

def _wells_item(store, legend):
    stac.write_item(stac.build_item(
        item_id="enmin_ucrc_wells", collection="ugs-serving-topics", geometry=None,
        bbox=[0, 1, 2, 3], datetime_iso=None, properties={"title": "wells"},
        assets={"pmtiles": {"href": "h", "type": "application/vnd.pmtiles"}}))
    return [{"itemId": "enmin_ucrc_wells", "render": "by-boxtype", "kind": "vector",
             "assets": ["pmtiles"], "path": "styles/x/by-boxtype.json", "legend": legend}]


def test_restyle_verify_passes_when_the_written_legend_matches(monkeypatch):
    store = _mem_gcs(monkeypatch)
    legend = [{"label": "Core", "color": "#5E3C99"}]
    _fake_manifest(monkeypatch, _wells_item(store, legend))
    assert R.restyle() == 1
    assert R._verify(R._scoped_groups("ugs-serving-topics")) == []


def test_restyle_verify_reports_drift_when_the_item_holds_an_older_legend(monkeypatch):
    """Simulates the real failure: the item on disk carries a legend the manifest no longer has."""
    import json

    store = _mem_gcs(monkeypatch)
    _fake_manifest(monkeypatch, _wells_item(store, [{"label": "Core", "color": "#5E3C99"}]))
    R.restyle()

    # Rewrite the item with the pre-publish (shaded) legend, as a stale bind would have.
    obj = stac.item_object_path("ugs-serving-topics", "enmin_ucrc_wells")
    item = json.loads(store[obj].decode())
    item["properties"]["ugs:renders"]["by-boxtype"]["legend"] = [{"label": "Core", "color": "#2C1C48"}]
    store[obj] = json.dumps(item).encode()

    assert R._verify(R._scoped_groups("ugs-serving-topics")) == ["ugs-serving-topics/enmin_ucrc_wells"]


def _styled_wells(monkeypatch) -> list:
    """A styled wells item in an in-memory bucket; returns the recorded thumbnail-job starts."""
    store = _mem_gcs(monkeypatch)
    _fake_manifest(monkeypatch, _wells_item(store, [{"label": "Core", "color": "#5E3C99"}]))
    kicked: list = []
    monkeypatch.setattr(R.jobs, "start_topic_thumbs", lambda item_ids=None: kicked.append(item_ids))
    return kicked


def test_a_restyle_that_rebinds_starts_the_thumbnail_job(monkeypatch):
    """A style change is a preview change; the thumbnail job re-renders what it affects."""
    kicked = _styled_wells(monkeypatch)
    R.restyle()
    assert kicked == [None]


def test_a_dry_run_restyle_starts_nothing(monkeypatch):
    kicked = _styled_wells(monkeypatch)
    R.restyle(dry_run=True)
    assert kicked == []


def test_a_restyle_with_nothing_styled_starts_nothing(monkeypatch):
    _mem_gcs(monkeypatch)
    _fake_manifest(monkeypatch, [])
    kicked: list = []
    monkeypatch.setattr(R.jobs, "start_topic_thumbs", lambda item_ids=None: kicked.append(item_ids))
    R.restyle()
    assert kicked == []
