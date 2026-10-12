import json

from ugs_warehouse.core import config, stac


def test_collection_object_path():
    assert stac.collection_object_path("ugs-publications/OFR/OFR-1") == (
        f"{config.STAC_PREFIX}/ugs-publications/OFR/OFR-1/collection.json")


def test_bbox_polygon_closes_the_ring():
    poly = stac.bbox_polygon([-112.0, 40.0, -111.0, 41.0])
    assert poly["type"] == "Polygon"
    ring = poly["coordinates"][0]
    assert ring[0] == ring[-1] == [-112.0, 40.0] and len(ring) == 5


def test_build_collection_links_assets_and_fields():
    coll = stac.build_collection(
        collection_id="OFR-1", collection_path="ugs-publications/OFR/OFR-1",
        title="A map", description="A map of somewhere.",
        extent={"spatial": {"bbox": [[-112.0, 40.0, -111.0, 41.0]]},
                "temporal": {"interval": [["2020-01-01T00:00:00Z", "2020-01-01T00:00:00Z"]]}},
        assets={"publication": {"href": "https://x/a.pdf", "type": "application/pdf", "roles": ["data"]}},
        item_titles={"OFR-1_plate": "Map plate", "OFR-1_faults": "Faults"},
        extra_links=[{"rel": "via", "href": "https://x/landing", "type": "text/html"}],
        fields={"ugs:series_id": "OFR-1", "version": "2"},
    )
    assert coll["type"] == "Collection" and coll["id"] == "OFR-1"
    assert coll["license"] == config.DATA_LICENSE and coll["providers"] == config.PROVIDERS
    assert coll["ugs:series_id"] == "OFR-1" and coll["version"] == "2"
    assert stac.VERSION_EXT in coll["stac_extensions"] and stac.PORTOLAN_SCHEMA in coll["stac_extensions"]
    rels = {(lk["rel"], lk["href"]) for lk in coll["links"]}
    # Three path segments above the collection file: ../../../catalog.json is the root.
    assert ("root", "../../../catalog.json") in rels
    assert ("parent", "../catalog.json") in rels and ("self", "./collection.json") in rels
    assert ("item", "./OFR-1_plate/OFR-1_plate.json") in rels
    assert ("via", "https://x/landing") in rels
    assert all(lk.get("title") for lk in coll["links"] if lk["rel"] == "item")
    assert coll["_collection_path"] == "ugs-publications/OFR/OFR-1"
    assert "ugs-items-index" not in {lk["rel"] for lk in coll["links"]}  # Stage 2 decides the index


def test_build_collection_with_no_items_has_no_item_links():
    coll = stac.build_collection(
        collection_id="C-1", collection_path="ugs-publications/C/C-1", title="A report",
        description="A report.", extent={"spatial": {"bbox": [stac.UTAH_BBOX]},
                                         "temporal": {"interval": [[None, None]]}},
        assets={"publication": {"href": "https://x/c.pdf", "type": "application/pdf", "roles": ["data"]}},
        item_titles={})
    assert not [lk for lk in coll["links"] if lk["rel"] == "item"]


def test_subcatalog_doc_at_series_depth_points_at_child_collections():
    doc = stac._subcatalog_doc("OFR", [{"id": "OFR-1", "title": "A map", "count": 3, "mappable": 1}],
                               depth=2, child_file="collection.json")
    rels = {(lk["rel"], lk["href"]) for lk in doc["links"]}
    assert ("root", "../../catalog.json") in rels and ("parent", "../catalog.json") in rels
    assert ("child", "./OFR-1/collection.json") in rels
    assert doc["type"] == "Catalog" and "license" not in doc


def test_subcatalog_doc_can_point_at_child_catalogs():
    doc = stac._subcatalog_doc("ugs-publications", [{"id": "OFR", "title": "Open File Report", "count": 9}],
                               child_file="catalog.json")
    assert ("child", "./OFR/catalog.json") in {(lk["rel"], lk["href"]) for lk in doc["links"]}


def _mem(monkeypatch):
    store = {}
    monkeypatch.setattr(stac.gcs, "put_bytes", lambda b, p, **kw: store.__setitem__(p, b))
    return store


def _coll(**over):
    base = dict(collection_id="OFR-1", collection_path="ugs-publications/OFR/OFR-1", title="A map",
                description="A map of somewhere.",
                extent={"spatial": {"bbox": [[-112.0, 40.0, -111.0, 41.0]]},
                        "temporal": {"interval": [["2020-01-01T00:00:00Z", "2020-01-01T00:00:00Z"]]}},
                assets={}, item_titles={"OFR-1_plate": "Map plate"},
                fields={"ugs:topic_category": "geoscientificInformation", "keywords": ["faults"]})
    return stac.build_collection(**{**base, **over})


def test_write_collection_writes_json_and_markdown_without_the_private_key(monkeypatch):
    store = _mem(monkeypatch)
    path = stac.write_collection(_coll(), children=1)
    assert path == stac.collection_object_path("ugs-publications/OFR/OFR-1")
    doc = json.loads(store[path])
    assert "_collection_path" not in doc and doc["id"] == "OFR-1"
    base = path.rsplit("/", 1)[0]
    assert f"{base}/README.md" in store and f"{base}/AGENTS.md" in store


def test_attach_collection_iso_writes_a_sidecar_and_a_metadata_asset(monkeypatch):
    store = _mem(monkeypatch)
    coll = _coll()
    path = stac.attach_collection_iso(coll)
    assert path.endswith("/ugs-publications/OFR/OFR-1/OFR-1.iso.xml")
    xml = store[path].decode()
    assert "A map" in xml and "-112.0" in xml
    assert coll["assets"]["metadata"]["roles"] == ["metadata", "iso-19115"]
    assert coll["assets"]["metadata"]["href"] == stac.config.public_url(path)
    assert "faults" in xml


def test_attach_collection_iso_warns_when_the_extent_is_open(monkeypatch, capsys):
    _mem(monkeypatch)
    coll = _coll(extent={"spatial": {"bbox": [[-112.0, 40.0, -111.0, 41.0]]},
                         "temporal": {"interval": [[None, None]]}})
    stac.attach_collection_iso(coll)
    assert "no temporal extent" in capsys.readouterr().err


def test_prior_collection_field_reads_the_published_collection(monkeypatch):
    path = stac.collection_object_path("ugs-publications/OFR/OFR-1")
    monkeypatch.setattr(stac.gcs, "get_bytes",
                        lambda p: json.dumps({"description": "Old citation."}).encode() if p == path else (_ for _ in ()).throw(FileNotFoundError(p)))
    assert stac.prior_collection_field("ugs-publications/OFR/OFR-1", "description") == "Old citation."
    assert stac.prior_collection_field("ugs-publications/OFR/OFR-2", "description") is None


def _memstore(monkeypatch):
    store = {}

    def get(p):
        if p not in store:
            raise FileNotFoundError(p)
        return store[p]

    monkeypatch.setattr(stac.gcs, "put_bytes", lambda b, p, **kw: store.__setitem__(p, b))
    monkeypatch.setattr(stac.gcs, "get_bytes", get)
    monkeypatch.setattr(stac.gcs, "list_paths", lambda prefix: [p for p in store if p.startswith(prefix)])
    monkeypatch.setattr(stac.item_mirror, "write", lambda path, items: None)
    return store


def _pub(store, group, series, sid, item_ids):
    coll = stac.build_collection(
        collection_id=sid, collection_path=f"{group}/{series}/{sid}", title=f"Pub {sid}",
        description=f"Pub {sid}.", extent={"spatial": {"bbox": [stac.UTAH_BBOX]},
                                           "temporal": {"interval": [[None, None]]}},
        assets={"publication": {"href": f"https://x/{sid}.pdf", "type": "application/pdf", "roles": ["data"]}},
        item_titles={i: i for i in item_ids}, fields={"ugs:pub_type": "Open File Report"})
    stac.write_collection(coll, children=len(item_ids))
    for i in item_ids:
        it = stac.build_item(item_id=i, collection=sid, collection_path=f"{group}/{series}/{sid}",
                             geometry=stac.bbox_polygon(stac.UTAH_BBOX), bbox=stac.UTAH_BBOX,
                             datetime_iso="2020-01-01T00:00:00Z", properties={"title": i}, assets={})
        stac.write_item(it)


def test_refresh_writes_series_catalogs_over_publication_collections(monkeypatch):
    store = _memstore(monkeypatch)
    _pub(store, "ugs-publications", "OFR", "OFR-1", ["OFR-1_plate"])
    _pub(store, "ugs-publications", "OFR", "OFR-2", [])            # report: no items
    _pub(store, "ugs-publications", "B", "B-1", [])
    _pub(store, "ugs-external", "B", "B-9", [])                     # same series code, other group
    before = store[stac.collection_object_path("ugs-publications/OFR/OFR-1")]

    stac.refresh_catalog()

    p = config.STAC_PREFIX
    series = json.loads(store[f"{p}/ugs-publications/OFR/catalog.json"])
    assert series["type"] == "Catalog"
    assert {lk["href"] for lk in series["links"] if lk["rel"] == "child"} == {
        "./OFR-1/collection.json", "./OFR-2/collection.json"}
    assert next(lk for lk in series["links"] if lk["href"] == "./OFR-1/collection.json")["ugs:item_count"] == 1
    top = json.loads(store[f"{p}/ugs-publications/catalog.json"])
    assert {lk["href"] for lk in top["links"] if lk["rel"] == "child"} == {"./OFR/catalog.json", "./B/catalog.json"}
    assert {lk["href"] for lk in json.loads(store[f"{p}/ugs-external/catalog.json"])["links"]
            if lk["rel"] == "child"} == {"./B/catalog.json"}
    # The pub collection is ingest's; refresh never rewrites it.
    assert store[stac.collection_object_path("ugs-publications/OFR/OFR-1")] == before
    assert f"{p}/ugs-publications/OFR/collection.json" not in store
    assert f"{p}/ugs-publications/OFR/OFR-1/items.json" not in store
    for node in ("ugs-publications", "ugs-publications/OFR", "ugs-external/B"):
        assert f"{p}/{node}/README.md" in store and f"{p}/{node}/AGENTS.md" in store
    root = json.loads(store[f"{p}/catalog.json"])
    assert {"./ugs-publications/catalog.json", "./ugs-external/catalog.json"} <= {
        lk["href"] for lk in root["links"] if lk["rel"] == "child"}
    index = json.loads(store[f"{p}/items.json"])
    assert [e["links"][0]["href"] for e in index["items"]] == [
        "./ugs-publications/OFR/OFR-1/OFR-1_plate/OFR-1_plate.json"]


def _old_layout_item(group, series, sid):
    return stac.build_item(item_id=sid, collection=series, collection_path=f"{group}/{series}",
                           geometry=stac.bbox_polygon(stac.UTAH_BBOX), bbox=stac.UTAH_BBOX,
                           datetime_iso="2020-01-01T00:00:00Z", properties={"title": sid}, assets={})


def test_refresh_indexes_publication_items_and_counts_mappable(monkeypatch):
    store = _memstore(monkeypatch)
    _pub(store, "ugs-publications", "OFR", "OFR-1", ["OFR-1_plate"])
    cog = {"cog": {"href": "https://x/p.tif", "type": "image/tiff; application=geotiff; profile=cloud-optimized",
                   "roles": ["data"]}}
    stac.write_item(stac.build_item(
        item_id="OFR-1_cog", collection="OFR-1", collection_path="ugs-publications/OFR/OFR-1",
        geometry=stac.bbox_polygon(stac.UTAH_BBOX), bbox=stac.UTAH_BBOX,
        datetime_iso="2020-01-01T00:00:00Z", properties={"title": "cog"}, assets=cog))

    stac.refresh_catalog()

    p = config.STAC_PREFIX
    index = json.loads(store[f"{p}/items.json"])
    hrefs = {e["links"][0]["href"] for e in index["items"]}
    assert len(hrefs) == 2 and all(h.startswith("./ugs-publications/OFR/OFR-1/") for h in hrefs)
    series = json.loads(store[f"{p}/ugs-publications/OFR/catalog.json"])
    assert series["ugs:mappable_count"] == 1
    root = json.loads(store[f"{p}/catalog.json"])
    link = next(lk for lk in root["links"] if lk.get("href") == "./ugs-publications/catalog.json")
    assert link["ugs:mappable_count"] == 1


def test_refresh_ignores_old_layout_publication_groups(monkeypatch, capsys):
    store = _memstore(monkeypatch)
    _pub(store, "ugs-publications", "OFR", "OFR-1", ["OFR-1_plate"])
    stac.write_item(_old_layout_item("ugs-publications", "OFR", "OFR-9"))

    stac.refresh_catalog()

    p = config.STAC_PREFIX
    root = json.loads(store[f"{p}/catalog.json"])
    assert [lk["href"] for lk in root["links"] if lk.get("href") == "./ugs-publications/catalog.json"] == [
        "./ugs-publications/catalog.json"]
    top = json.loads(store[f"{p}/ugs-publications/catalog.json"])
    assert [lk["href"] for lk in top["links"] if lk["rel"] == "child"] == ["./OFR/catalog.json"]
    assert f"{p}/ugs-publications/OFR/collection.json" not in store
    assert f"{p}/ugs-publications/OFR/items.json" not in store
    assert not any(e["id"] == "OFR-9" for e in json.loads(store[f"{p}/items.json"])["items"])
    assert "old-layout publication series awaiting prune --legacy: ugs-publications/OFR" in capsys.readouterr().err


def test_refresh_finishes_then_reports_publication_items_without_a_collection(monkeypatch):
    import pytest

    store = _memstore(monkeypatch)
    _pub(store, "ugs-publications", "OFR", "OFR-1", ["OFR-1_plate"])
    stac.write_item(stac.build_item(
        item_id="X-1_plate", collection="X-1", collection_path="ugs-publications/OFR/X-1",
        geometry=stac.bbox_polygon(stac.UTAH_BBOX), bbox=stac.UTAH_BBOX,
        datetime_iso="2020-01-01T00:00:00Z", properties={"title": "x"}, assets={}))

    with pytest.raises(RuntimeError, match="ugs-publications/OFR/X-1"):
        stac.refresh_catalog()

    p = config.STAC_PREFIX
    assert f"{p}/catalog.json" in store and f"{p}/items.json" in store
    series = json.loads(store[f"{p}/ugs-publications/OFR/catalog.json"])
    assert [lk["href"] for lk in series["links"] if lk["rel"] == "child"] == ["./OFR-1/collection.json"]
    assert not any(e["id"] == "X-1_plate" for e in json.loads(store[f"{p}/items.json"])["items"])
