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
                               depth=2)
    rels = {(lk["rel"], lk["href"]) for lk in doc["links"]}
    assert ("root", "../../catalog.json") in rels and ("parent", "../catalog.json") in rels
    assert ("child", "./OFR-1/collection.json") in rels
    assert doc["type"] == "Catalog" and "license" not in doc


def test_subcatalog_doc_points_at_each_childs_own_file():
    doc = stac._subcatalog_doc("ugs-publications", [
        {"id": "OFR", "title": "Open File Report", "count": 9, "file": "catalog.json"},
        {"id": "B", "title": "Bulletin", "count": 2}])
    rels = {(lk["rel"], lk["href"]) for lk in doc["links"]}
    assert ("child", "./OFR/catalog.json") in rels and ("child", "./B/collection.json") in rels


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


def test_write_collection_stores_json_uncompressed(monkeypatch):
    flags = {}
    monkeypatch.setattr(stac.gcs, "put_bytes",
                        lambda b, p, **kw: flags.__setitem__(p, kw.get("compress")))
    path = stac.write_collection(_coll(), children=1)
    assert flags[path] is False


def test_write_json_compresses_by_default(monkeypatch):
    flags = {}
    monkeypatch.setattr(stac.gcs, "put_bytes",
                        lambda b, p, **kw: flags.__setitem__(p, kw.get("compress")))
    stac._write_json({"type": "Catalog"}, "x/catalog.json")
    assert flags["x/catalog.json"] is True


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


def test_prior_property_is_none_only_when_the_item_is_missing(monkeypatch):
    import pytest

    monkeypatch.setattr(stac.gcs, "get_bytes", lambda p: (_ for _ in ()).throw(FileNotFoundError(p)))
    assert stac.prior_property("ugs-publications/OFR", "OFR-1", "description") is None
    monkeypatch.setattr(stac.gcs, "get_bytes", lambda p: (_ for _ in ()).throw(OSError("503 from GCS")))
    with pytest.raises(OSError, match="503"):
        stac.prior_property("ugs-publications/OFR", "OFR-1", "description")


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


def _pub(store, group, series, sid, item_ids, assets=None, fields=None, extent=None):
    coll = stac.build_collection(
        collection_id=sid, collection_path=f"{group}/{series}/{sid}", title=f"Pub {sid}",
        description=f"Pub {sid}.",
        extent=extent or {"spatial": {"bbox": [stac.UTAH_BBOX]},
                          "temporal": {"interval": [[None, None]]}},
        assets=assets or {"publication": {"href": f"https://x/{sid}.pdf", "type": "application/pdf",
                                          "roles": ["data"]}},
        item_titles={i: i for i in item_ids},
        fields={"ugs:pub_type": "Open File Report", **(fields or {})})
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
    # The generated docs describe the new indexes: publications plus tagged Items at the root,
    # publications only in a series.
    root_agents = store[f"{p}/AGENTS.md"].decode()
    assert "each publication once" in root_agents and "`ugs:in_publication`" in root_agents
    series_agents = store[f"{p}/ugs-publications/OFR/AGENTS.md"].decode()
    assert "lists this series' publications" in series_agents
    assert "lists the publications in this series" in store[f"{p}/ugs-publications/OFR/README.md"].decode()
    assert "publications in this series" not in store[f"{p}/ugs-publications/README.md"].decode()
    root = json.loads(store[f"{p}/catalog.json"])
    assert {"./ugs-publications/catalog.json", "./ugs-external/catalog.json"} <= {
        lk["href"] for lk in root["links"] if lk["rel"] == "child"}
    index = json.loads(store[f"{p}/items.json"])
    assert [e["links"][0]["href"] for e in index["items"]] == [
        "./ugs-external/B/B-9/collection.json", "./ugs-publications/B/B-1/collection.json",
        "./ugs-publications/OFR/OFR-1/OFR-1_plate/OFR-1_plate.json",
        "./ugs-publications/OFR/OFR-1/collection.json", "./ugs-publications/OFR/OFR-2/collection.json"]


def test_a_report_only_publication_appears_in_the_root_index(monkeypatch):
    store = _memstore(monkeypatch)
    _pub(store, "ugs-publications", "OFR", "OFR-2", [])

    stac.refresh_catalog()

    index = json.loads(store[f"{config.STAC_PREFIX}/items.json"])
    assert [e["id"] for e in index["items"]] == ["OFR-2"]
    assert index["items"][0]["assets"]["publication"]["href"] == "https://x/OFR-2.pdf"
    assert index["items"][0]["properties"]["ugs:item_count"] == 0


def test_a_publication_with_many_items_is_one_root_entry_with_its_facets_and_pdf(monkeypatch):
    store = _memstore(monkeypatch)
    ids = [f"OFR-9_layer{n}" for n in range(19)]
    _pub(store, "ugs-publications", "OFR", "OFR-9", ids,
         assets={"publication": {"href": "https://x/OFR-9.pdf", "type": "application/pdf",
                                 "roles": ["data"], "ugs:private": 1},
                 "thumbnail": {"href": "https://x/OFR-9.webp", "type": "image/webp",
                               "roles": ["thumbnail"]}},
         fields={"ugs:series_id": "OFR-9", "ugs:series": "OFR", "ugs:topic": "Hazards",
                 "ugs:author": "A. Smith", "keywords": ["landslide"], "ugs:scale": "1:24,000",
                 "ugs:county": ["Utah"], "ugs:volume": 3, "ugs:topic_category": "dropped"},
         extent={"spatial": {"bbox": [[-112.5, 40.5, -112.0, 41.0]]},
                 "temporal": {"interval": [["2020-01-01T00:00:00Z", "2020-01-01T00:00:00Z"]]}})

    stac.refresh_catalog()

    p = config.STAC_PREFIX
    entries = json.loads(store[f"{p}/items.json"])["items"]
    (entry,) = [e for e in entries if "ugs:in_publication" not in e["properties"]]
    tagged = [e["id"] for e in entries if e["properties"].get("ugs:in_publication") == "OFR-9"]
    assert sorted(tagged) == sorted(ids)
    assert entry == {
        "id": "OFR-9", "bbox": [-112.5, 40.5, -112.0, 41.0],
        "properties": {
            "title": "Pub OFR-9", "datetime": "2020-01-01T00:00:00Z", "ugs:series_id": "OFR-9",
            "ugs:series": "OFR", "ugs:pub_type": "Open File Report", "ugs:topic": "Hazards",
            "ugs:scale": "1:24,000", "ugs:author": "A. Smith", "ugs:county": ["Utah"],
            "ugs:volume": 3, "keywords": ["landslide"], "ugs:item_count": 19},
        "assets": {"publication": {"href": "https://x/OFR-9.pdf", "type": "application/pdf",
                                   "roles": ["data"]},
                   "thumbnail": {"href": "https://x/OFR-9.webp", "type": "image/webp",
                                 "roles": ["thumbnail"]}},
        "links": [{"rel": "self", "href": "./ugs-publications/OFR/OFR-9/collection.json",
                   "type": "application/json"}]}


def test_index_entries_carry_no_bbox_without_a_footprint():
    placed = {"id": "a", "geometry": {"type": "Point", "coordinates": [0, 0]}, "bbox": [0, 0, 0, 0],
              "properties": {"ugs:footprint_source": "cog"}}
    assert stac._index_entry(placed)["bbox"] == [0, 0, 0, 0]
    table = {"id": "t", "geometry": None, "properties": {}}
    assert "bbox" not in stac._index_entry(table)
    on_utah = {**placed, "bbox": stac.UTAH_BBOX, "properties": {"ugs:footprint_source": "utah-bbox"}}
    assert "bbox" not in stac._index_entry(on_utah)
    coll = {"id": "C-139", "links": [], "ugs:footprint_source": "utah-bbox",
            "extent": {"spatial": {"bbox": [stac.UTAH_BBOX]}, "temporal": {"interval": [[None, None]]}}}
    assert "bbox" not in stac._collection_index_entry(coll, prefix="C-139/")
    coll_real = {**coll, "ugs:footprint_source": "cog"}
    assert stac._collection_index_entry(coll_real, prefix="C-139/")["bbox"] == stac.UTAH_BBOX


def test_an_undated_publication_entry_carries_the_marker_not_a_datetime():
    coll = {"id": "OFR-0", "title": "Old", "ugs:date_unknown": True, "links": [],
            "extent": {"spatial": {"bbox": [stac.UTAH_BBOX]},
                       "temporal": {"interval": [["1886-01-01T00:00:00Z", "2026-10-12T00:00:00Z"]]}}}
    props = stac._collection_index_entry(coll, prefix="OFR-0/")["properties"]
    assert props["ugs:date_unknown"] is True and "datetime" not in props


def test_a_series_catalog_publishes_an_items_index_of_its_publications(monkeypatch):
    store = _memstore(monkeypatch)
    _pub(store, "ugs-publications", "OFR", "OFR-1", ["OFR-1_plate"])
    _pub(store, "ugs-publications", "OFR", "OFR-2", [])

    stac.refresh_catalog()

    p = config.STAC_PREFIX
    series = json.loads(store[f"{p}/ugs-publications/OFR/catalog.json"])
    link = next(lk for lk in series["links"] if lk["rel"] == stac.INDEX_REL)
    assert link["href"] == "./items.json"
    index = json.loads(store[f"{p}/ugs-publications/OFR/items.json"])
    assert index["type"] == "ugs-items-index" and index["collection"] == "OFR" and index["count"] == 2
    assert [e["links"][0]["href"] for e in index["items"]] == [
        "./OFR-1/collection.json", "./OFR-2/collection.json"]
    assert [e["properties"]["ugs:item_count"] for e in index["items"]] == [1, 0]


def test_a_pending_series_is_unchanged_by_a_migrated_series_beside_it(monkeypatch):
    store = _memstore(monkeypatch)
    _old_pub("ugs-publications", "OFR", "OFR-1")
    _old_pub("ugs-publications", "OFR", "OFR-2")
    stac.refresh_catalog()
    p = config.STAC_PREFIX
    pending = {k: v for k, v in store.items() if k.startswith(f"{p}/ugs-publications/OFR/")}

    _pub(store, "ugs-publications", "B", "B-1", ["B-1_plate"])
    stac.refresh_catalog()

    assert {k: store[k] for k in pending} == pending
    assert not any(k.startswith(f"{p}/ugs-publications/OFR/") and k not in pending for k in store)
    assert f"{p}/ugs-publications/OFR/catalog.json" not in store


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
    assert [e["links"][0]["href"] for e in index["items"]] == [
        "./ugs-publications/OFR/OFR-1/OFR-1_cog/OFR-1_cog.json",
        "./ugs-publications/OFR/OFR-1/OFR-1_plate/OFR-1_plate.json",
        "./ugs-publications/OFR/OFR-1/collection.json"]
    assert index["items"][2]["properties"]["ugs:item_count"] == 2
    cog_entry = index["items"][0]
    assert cog_entry["properties"]["ugs:in_publication"] == "OFR-1"
    assert cog_entry["properties"]["ugs:publication_title"] == "Pub OFR-1"
    assert cog_entry["assets"]["cog"]["href"] == "https://x/p.tif"
    series = json.loads(store[f"{p}/ugs-publications/OFR/catalog.json"])
    assert series["ugs:mappable_count"] == 1
    root = json.loads(store[f"{p}/catalog.json"])
    link = next(lk for lk in root["links"] if lk.get("href") == "./ugs-publications/catalog.json")
    assert link["ugs:mappable_count"] == 1


def test_refresh_counts_a_single_file_plate_as_mappable(monkeypatch):
    store = _memstore(monkeypatch)
    cog = {"cog": {"href": "https://x/p.cog.tif", "type": config.COG_MIME, "roles": ["data", "cloud-optimized"]}}
    _pub(store, "ugs-publications", "M", "M-1", [], assets=cog)   # plate on the collection, no items
    _pub(store, "ugs-publications", "M", "M-2", [])               # report only

    stac.refresh_catalog()

    p = config.STAC_PREFIX
    series = json.loads(store[f"{p}/ugs-publications/M/catalog.json"])
    assert series["ugs:mappable_count"] == 1
    kids = {lk["href"]: lk for lk in series["links"] if lk["rel"] == "child"}
    assert kids["./M-1/collection.json"]["ugs:mappable_count"] == 1
    assert kids["./M-2/collection.json"]["ugs:mappable_count"] == 0


def _old_pub(group, series, sid):
    stac.write_item(_old_layout_item(group, series, sid))


def test_refresh_indexes_unmigrated_series_in_the_old_layout(monkeypatch, capsys):
    store = _memstore(monkeypatch)
    _old_pub("ugs-publications", "OFR", "OFR-1")
    _old_pub("ugs-publications", "OFR", "OFR-2")
    _old_pub("ugs-publications", "B", "B-1")

    assert stac.refresh_catalog() == []

    p = config.STAC_PREFIX
    root = json.loads(store[f"{p}/catalog.json"])
    link = next(lk for lk in root["links"] if lk.get("href") == "./ugs-publications/catalog.json")
    assert link["ugs:item_count"] == 3
    top = json.loads(store[f"{p}/ugs-publications/catalog.json"])
    assert {lk["href"] for lk in top["links"] if lk["rel"] == "child"} == {
        "./OFR/collection.json", "./B/collection.json"}
    coll = json.loads(store[f"{p}/ugs-publications/OFR/collection.json"])
    assert {lk["href"] for lk in coll["links"] if lk["rel"] == "item"} == {
        "./OFR-1/OFR-1.json", "./OFR-2/OFR-2.json"}
    assert coll["description"] == stac.SERIES_DESC["OFR"]
    assert json.loads(store[f"{p}/ugs-publications/OFR/items.json"])["count"] == 2
    assert f"{p}/ugs-publications/OFR/catalog.json" not in store
    assert [e["links"][0]["href"] for e in json.loads(store[f"{p}/items.json"])["items"]] == [
        "./ugs-publications/B/B-1/B-1.json", "./ugs-publications/OFR/OFR-1/OFR-1.json",
        "./ugs-publications/OFR/OFR-2/OFR-2.json"]
    assert "ugs-publications/OFR: 2 publication(s) not yet in the collection layout" in capsys.readouterr().err


def test_refresh_keeps_a_partly_migrated_series_in_the_old_layout(monkeypatch, capsys):
    store = _memstore(monkeypatch)
    _old_pub("ugs-publications", "OFR", "OFR-1")
    _old_pub("ugs-publications", "OFR", "OFR-2")
    _pub(store, "ugs-publications", "OFR", "OFR-1", ["OFR-1_plate"])   # migrated; OFR-2 is not

    assert stac.refresh_catalog() == []

    p = config.STAC_PREFIX
    assert f"{p}/ugs-publications/OFR/catalog.json" not in store
    top = json.loads(store[f"{p}/ugs-publications/catalog.json"])
    assert [lk["href"] for lk in top["links"] if lk["rel"] == "child"] == ["./OFR/collection.json"]
    coll = json.loads(store[f"{p}/ugs-publications/OFR/collection.json"])
    assert {lk["href"] for lk in coll["links"] if lk["rel"] == "item"} == {
        "./OFR-1/OFR-1.json", "./OFR-2/OFR-2.json"}
    hrefs = [e["links"][0]["href"] for e in json.loads(store[f"{p}/items.json"])["items"]]
    assert hrefs == ["./ugs-publications/OFR/OFR-1/OFR-1.json", "./ugs-publications/OFR/OFR-2/OFR-2.json"]
    assert ("ugs-publications/OFR: 1 publication(s) not yet in the collection layout, "
            "series stays in the old layout: OFR-2") in capsys.readouterr().err


def test_refresh_switches_a_complete_series_to_its_catalog(monkeypatch):
    store = _memstore(monkeypatch)
    _old_pub("ugs-publications", "OFR", "OFR-1")
    _old_pub("ugs-publications", "OFR", "OFR-2")
    _pub(store, "ugs-publications", "OFR", "OFR-1", ["OFR-1_plate"])
    _pub(store, "ugs-publications", "OFR", "OFR-2", [])

    assert stac.refresh_catalog() == []

    p = config.STAC_PREFIX
    series = json.loads(store[f"{p}/ugs-publications/OFR/catalog.json"])
    assert {lk["href"] for lk in series["links"] if lk["rel"] == "child"} == {
        "./OFR-1/collection.json", "./OFR-2/collection.json"}
    top = json.loads(store[f"{p}/ugs-publications/catalog.json"])
    assert [lk["href"] for lk in top["links"] if lk["rel"] == "child"] == ["./OFR/catalog.json"]
    assert f"{p}/ugs-publications/OFR/collection.json" not in store
    assert [e["links"][0]["href"] for e in json.loads(store[f"{p}/items.json"])["items"]] == [
        "./ugs-publications/OFR/OFR-1/OFR-1_plate/OFR-1_plate.json",
        "./ugs-publications/OFR/OFR-1/collection.json", "./ugs-publications/OFR/OFR-2/collection.json"]


def test_refresh_links_migrated_and_unmigrated_series_in_one_group(monkeypatch):
    store = _memstore(monkeypatch)
    _pub(store, "ugs-publications", "B", "B-1", ["B-1_plate"])
    _old_pub("ugs-publications", "OFR", "OFR-1")

    stac.refresh_catalog()

    p = config.STAC_PREFIX
    top = json.loads(store[f"{p}/ugs-publications/catalog.json"])
    kids = {lk["href"]: lk for lk in top["links"] if lk["rel"] == "child"}
    assert set(kids) == {"./B/catalog.json", "./OFR/collection.json"}
    assert kids["./B/catalog.json"]["ugs:item_count"] == 1
    assert kids["./OFR/collection.json"]["ugs:item_count"] == 1
    assert top["ugs:item_count"] == 2
    assert f"{p}/ugs-publications/B/catalog.json" in store
    assert f"{p}/ugs-publications/OFR/collection.json" in store
    assert {e["links"][0]["href"] for e in json.loads(store[f"{p}/items.json"])["items"]} == {
        "./ugs-publications/B/B-1/collection.json", "./ugs-publications/B/B-1/B-1_plate/B-1_plate.json",
        "./ugs-publications/OFR/OFR-1/OFR-1.json"}


def _orphan():
    stac.write_item(stac.build_item(
        item_id="X-1_plate", collection="X-1", collection_path="ugs-publications/OFR/X-1",
        geometry=stac.bbox_polygon(stac.UTAH_BBOX), bbox=stac.UTAH_BBOX,
        datetime_iso="2020-01-01T00:00:00Z", properties={"title": "x"}, assets={}))


def test_refresh_reports_publication_items_without_a_collection_and_finishes(monkeypatch, capsys):
    store = _memstore(monkeypatch)
    _pub(store, "ugs-publications", "OFR", "OFR-1", ["OFR-1_plate"])
    _orphan()

    problems = stac.refresh_catalog()

    assert problems == ["publication items with no collection.json: ugs-publications/OFR/X-1"]
    assert "ugs-publications/OFR/X-1" in capsys.readouterr().err
    p = config.STAC_PREFIX
    assert f"{p}/catalog.json" in store and f"{p}/items.json" in store
    series = json.loads(store[f"{p}/ugs-publications/OFR/catalog.json"])
    assert [lk["href"] for lk in series["links"] if lk["rel"] == "child"] == ["./OFR-1/collection.json"]
    assert not any(e["id"] == "X-1_plate" for e in json.loads(store[f"{p}/items.json"])["items"])


def test_refresh_strict_raises_after_writing_the_catalog(monkeypatch):
    import pytest

    store = _memstore(monkeypatch)
    _pub(store, "ugs-publications", "OFR", "OFR-1", ["OFR-1_plate"])
    _orphan()

    with pytest.raises(RuntimeError, match="ugs-publications/OFR/X-1"):
        stac.refresh_catalog(strict=True)

    p = config.STAC_PREFIX
    assert f"{p}/catalog.json" in store and f"{p}/ugs-publications/OFR/catalog.json" in store


def test_refresh_counts_only_publication_items_that_load_and_reports_the_rest(monkeypatch, capsys):
    store = _memstore(monkeypatch)
    _pub(store, "ugs-publications", "OFR", "OFR-1", ["OFR-1_plate", "OFR-1_units"])
    store[stac.item_object_path("ugs-publications/OFR/OFR-1", "OFR-1_units")] = b"{not json"

    problems = stac.refresh_catalog()

    assert len(problems) == 1
    assert problems[0].startswith("dropped ugs-publications/OFR/OFR-1/OFR-1_units from the index")
    p = config.STAC_PREFIX
    root = json.loads(store[f"{p}/items.json"])["items"]
    assert [e["id"] for e in root] == ["OFR-1_plate", "OFR-1"]
    assert root[1]["properties"]["ugs:item_count"] == 1
    series = json.loads(store[f"{p}/ugs-publications/OFR/catalog.json"])
    assert next(lk for lk in series["links"] if lk["rel"] == "child")["ugs:item_count"] == 1
    assert "(1 collections, 1 items)" in capsys.readouterr().out


def test_refresh_skips_an_unreadable_publication_collection(monkeypatch, capsys):
    store = _memstore(monkeypatch)
    _pub(store, "ugs-publications", "OFR", "OFR-1", ["OFR-1_plate"])
    _pub(store, "ugs-publications", "OFR", "OFR-2", ["OFR-2_plate"])
    store[stac.collection_object_path("ugs-publications/OFR/OFR-2")] = b"{not json"

    problems = stac.refresh_catalog()

    assert len(problems) == 1
    assert problems[0].startswith("unreadable collection.json: ugs-publications/OFR/OFR-2")
    assert "ugs-publications/OFR/OFR-2" in capsys.readouterr().err
    p = config.STAC_PREFIX
    series = json.loads(store[f"{p}/ugs-publications/OFR/catalog.json"])
    assert [lk["href"] for lk in series["links"] if lk["rel"] == "child"] == ["./OFR-1/collection.json"]
    # The unreadable publication's Items go with it: there is no publication to tag them with.
    assert [e["links"][0]["href"] for e in json.loads(store[f"{p}/items.json"])["items"]] == [
        "./ugs-publications/OFR/OFR-1/OFR-1_plate/OFR-1_plate.json",
        "./ugs-publications/OFR/OFR-1/collection.json"]
