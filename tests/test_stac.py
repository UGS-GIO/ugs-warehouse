"""Shared STAC builders — the collections-layout catalog (derive-from-truth refresh)."""
from unittest.mock import patch

from ugs_warehouse.core import config, iso, stac
from ugs_warehouse.pubs import identity
from ugs_warehouse.pubs import sink_stac as pubs_sink
from ugs_warehouse.pubs.sink_stac import collection_group


def test_pub_items_author_an_iso_topic_category():
    """Pubs share core/iso.py with the vector path, which omits topicCategory when uncurated (#53).

    Pubs have no schema_registry row, so without an authored value every publication's ISO record
    would silently lose a mandatory element. A UGS publication is our own product — asserting the
    category is a statement about our own work, not a guess about someone else's data.
    """
    with patch("ugs_warehouse.core.stac.prior_property", return_value=""), \
         patch("ugs_warehouse.core.stac.manual_override", return_value={}):
        item = pubs_sink.build_item({"series_id": "DS-8", "pub_name": "Test Pub", "series": "DS"}, [])

    assert item["properties"]["ugs:topic_category"] == "geoscientificInformation"
    assert "<gmd:topicCategory>" in iso.stac_to_iso19139(item)


def test_collection_group_routes_md_external_and_ugs():
    # Mining District Files → their own collection regardless of publisher.
    assert collection_group({"series_id": "MD-100", "pub_publisher": "USGS"}) == identity.MINING_DISTRICT_COLLECTION
    # UGS / UGMS / blank publisher → the main UGS catalog.
    assert collection_group({"series_id": "OFR-1", "pub_publisher": "UGS"}) == identity.PUBLICATIONS_COLLECTION
    assert collection_group({"series_id": "B-1", "pub_publisher": "UGMS"}) == identity.PUBLICATIONS_COLLECTION
    assert collection_group({"series_id": "M-1", "pub_publisher": ""}) == identity.PUBLICATIONS_COLLECTION
    # USGS-authored Utah geologic quads stay in the main catalog (the COG-worthy maps).
    assert collection_group({"series_id": "GQ-968", "pub_publisher": "USGS"}) == identity.PUBLICATIONS_COLLECTION
    # Foreign publishers UGS only hosts → external.
    assert collection_group({"series_id": "X-1", "pub_publisher": "BYU"}) == identity.EXTERNAL_COLLECTION


def test_bbox_polygon_is_closed_ring():
    poly = stac.bbox_polygon([0, 1, 2, 3])
    assert poly["type"] == "Polygon"
    ring = poly["coordinates"][0]
    assert ring[0] == [0, 1] and ring[2] == [2, 3]
    assert ring[0] == ring[-1]  # closed


def test_prettify_titlecases_snake_id():
    assert stac.prettify("geothermal_kgra") == "Geothermal Kgra"
    assert stac.prettify("enmin_ccus_cbcounty") == "Enmin Ccus Cbcounty"


def test_pmtiles_link():
    link = stac.pmtiles_link("http://x/a.pmtiles", ["lyr"])
    assert link["rel"] == "pmtiles"
    assert link["type"] == "application/vnd.pmtiles"
    assert link["pmtiles:layers"] == ["lyr"]


def test_build_item_has_nested_links_and_datetime():
    item = stac.build_item(
        item_id="x", collection="ugs-serving-topics", geometry=None, bbox=[0, 1, 2, 3],
        datetime_iso="2026-01-01T00:00:00Z", properties={"a": 1}, assets={},
        extra_links=[{"rel": "pmtiles", "href": "h"}], stac_extensions=[stac.WEB_MAP_LINKS_EXT],
    )
    assert item["id"] == "x" and item["collection"] == "ugs-serving-topics"
    rels = {link["rel"] for link in item["links"]}
    assert {"root", "parent", "collection", "self", "pmtiles"} <= rels
    assert item["properties"]["datetime"] == "2026-01-01T00:00:00Z"
    assert item["properties"]["a"] == 1
    assert item["stac_extensions"] == [stac.WEB_MAP_LINKS_EXT]


def test_build_item_proj_extension():
    item = stac.build_item(
        item_id="x", collection="c", geometry=None, bbox=[0, 1, 2, 3],
        datetime_iso=None, properties={}, assets={}, proj_epsg=4326,
    )
    assert item["properties"]["proj:code"] == "EPSG:4326"
    assert "proj:epsg" not in item["properties"]   # full swap to v2.0.0
    assert stac.PROJ_EXT in item["stac_extensions"]


def test_extent_unions_bboxes_and_datetimes():
    items = [
        {"bbox": [-114, 37, -111, 40], "properties": {"datetime": "2026-01-01T00:00:00Z"}},
        {"bbox": [-112, 38, -109, 42], "properties": {"datetime": "2026-06-01T00:00:00Z"}},
    ]
    ext = stac._extent(items)
    assert ext["spatial"]["bbox"] == [[-114, 37, -109, 42]]
    assert ext["temporal"]["interval"] == [["2026-01-01T00:00:00Z", "2026-06-01T00:00:00Z"]]


def test_extent_falls_back_to_utah_when_no_bbox():
    ext = stac._extent([{"properties": {}}])
    assert ext["spatial"]["bbox"] == [stac.UTAH_BBOX]
    assert ext["temporal"]["interval"] == [[None, None]]


def test_root_doc_sorts_child_hrefs(monkeypatch):
    monkeypatch.setattr(stac.config, "EXTERNAL_CATALOGS", [])  # isolate local children
    doc = stac._root_doc([{"href": "./b/collection.json", "title": "B", "count": 2},
                          {"href": "./a/collection.json", "title": "A", "count": 1}])
    assert doc["type"] == "Catalog"
    kids = [(lnk["href"], lnk.get("title"), lnk.get("ugs:item_count"))
            for lnk in doc["links"] if lnk["rel"] == "child"]
    assert kids == [("./a/collection.json", "A", 1), ("./b/collection.json", "B", 2)]


def test_root_doc_federates_prod_only_in_review(monkeypatch):
    kids = [{"href": "./a/collection.json", "title": "A", "count": 1}]
    monkeypatch.setattr(stac.config, "EXTERNAL_CATALOGS", [])  # isolate the prod-self link

    # Public catalog: no external federation link.
    monkeypatch.setattr(stac.config, "IS_REVIEW_CATALOG", False)
    hrefs = [lnk["href"] for lnk in stac._root_doc(kids)["links"] if lnk["rel"] == "child"]
    assert not any(h.startswith("http") for h in hrefs)

    # Review catalog: adds a rel=child link to the public prod catalog (federated, not duplicated).
    monkeypatch.setattr(stac.config, "IS_REVIEW_CATALOG", True)
    monkeypatch.setattr(stac.config, "PUBLIC_CATALOG_URL", "https://cdn.example/warehouse/stac/catalog.json")
    links = stac._root_doc(kids)["links"]
    prod = [lnk for lnk in links if lnk["rel"] == "child" and lnk["href"].startswith("http")]
    assert len(prod) == 1
    assert prod[0]["href"] == "https://cdn.example/warehouse/stac/catalog.json"


def test_root_doc_federates_external_catalogs(monkeypatch):
    # EXTERNAL_CATALOGS (e.g. USWB) become rel=child links of the root, in BOTH prod
    # and review (unlike the prod-self link, which is review-only).
    monkeypatch.setattr(stac.config, "EXTERNAL_CATALOGS",
                        [("https://ext.example/stac/catalog.json", "Ext")])
    monkeypatch.setattr(stac.config, "IS_REVIEW_CATALOG", False)
    links = stac._root_doc([{"href": "./a/collection.json", "title": "A", "count": 1}])["links"]
    ext = [lnk for lnk in links if lnk["rel"] == "child" and lnk["href"].startswith("http")]
    assert len(ext) == 1
    assert ext[0]["href"] == "https://ext.example/stac/catalog.json"
    assert ext[0]["title"] == "Ext"


def test_collection_doc_sorts_items_and_links_the_service_root():
    doc = stac._collection_doc("ugs-geologic-maps", "ugs-geologic-maps", ["i2", "i1"])
    assert doc["type"] == "Collection"
    items = [link["href"] for link in doc["links"] if link["rel"] == "item"]
    assert items == ["./i1/i1.json", "./i2/i2.json"]
    # The service root, NOT /collections/<collection>: featureserv keys its collections by STAC
    # item id, so a per-collection path 404s on every host (it named nothing that ever existed).
    svc = [lnk["href"] for lnk in doc["links"] if lnk["rel"] == "service"]
    assert svc == [f"{stac.PGF_BASE_URL}/collections"]
    assert any(lnk["rel"] == "root" and lnk["href"] == "../catalog.json" for lnk in doc["links"])


def test_nested_serving_topic_collection_keeps_a_service_link():
    """The schema collections ARE what featureserv serves — depth alone must not hide the link."""
    doc = stac._collection_doc("hazards", "ugs-serving-topics/hazards", ["hazards_qfaults"])
    assert [lnk["href"] for lnk in doc["links"] if lnk["rel"] == "service"] \
        == [f"{stac.PGF_BASE_URL}/collections"]


def test_collection_doc_nested_series_depth_and_no_service():
    doc = stac._collection_doc("DS", "ugs-publications/DS", ["DS-2", "DS-1"], title="Data Series")
    assert doc["id"] == "DS" and doc["title"] == "Data Series"
    assert any(lnk["rel"] == "root" and lnk["href"] == "../../catalog.json" for lnk in doc["links"])
    assert any(lnk["rel"] == "parent" and lnk["href"] == "../catalog.json" for lnk in doc["links"])
    assert not any(lnk["rel"] == "service" for lnk in doc["links"])


def test_subcatalog_doc_children():
    doc = stac._subcatalog_doc("ugs-publications",
                               [{"id": "OFR", "title": "Open File Report", "count": 3},
                                {"id": "DS", "title": "Data Series", "count": 2}],
                               title="Publications")
    assert doc["type"] == "Catalog"
    assert doc["ugs:item_count"] == 5  # top-level prefixed extra, not `summaries` (Catalogs lack it)
    kids = [(lnk["href"], lnk.get("title"), lnk.get("ugs:item_count"))
            for lnk in doc["links"] if lnk["rel"] == "child"]
    assert kids == [("./DS/collection.json", "Data Series", 2),
                    ("./OFR/collection.json", "Open File Report", 3)]


def test_group_items_nested_series_paths():
    p = config.STAC_PREFIX
    paths = [
        f"{p}/ugs-publications/DS/DS-8/DS-8.json",
        f"{p}/ugs-publications/OFR/OFR-1/OFR-1.json",
        f"{p}/ugs-publications/DS/collection.json",
        f"{p}/ugs-publications/catalog.json",
    ]
    groups = stac._group_items(paths)
    assert groups["ugs-publications/DS"] == ["DS-8"]
    assert groups["ugs-publications/OFR"] == ["OFR-1"]


def test_group_items_keeps_only_nested_item_paths():
    p = config.STAC_PREFIX
    paths = [
        f"{p}/ugs-serving-topics/x/x.json",
        f"{p}/ugs-serving-topics/y/y.json",
        f"{p}/catalog.json",                          # root, skipped
        f"{p}/ugs-serving-topics/collection.json",    # collection doc, skipped
    ]
    groups = stac._group_items(paths)
    assert sorted(groups["ugs-serving-topics"]) == ["x", "y"]


def test_group_items_nested_serving_topic_paths():
    p = config.STAC_PREFIX
    paths = [
        f"{p}/ugs-serving-topics/hazards/hazards_qfaults/hazards_qfaults.json",
        f"{p}/ugs-serving-topics/emp/enmin_ucrc_wells/enmin_ucrc_wells.json",
        f"{p}/ugs-serving-topics/items.json",             # rollup index, skipped
        f"{p}/ugs-serving-topics/hazards/items.json",     # per-collection index, skipped
        f"{p}/ugs-serving-topics/catalog.json",           # sub-catalog doc, skipped
    ]
    groups = stac._group_items(paths)
    assert groups == {"ugs-serving-topics/hazards": ["hazards_qfaults"],
                      "ugs-serving-topics/emp": ["enmin_ucrc_wells"]}


def test_subcatalog_items_index_link_is_opt_in():
    kids = [{"id": "hazards", "title": "Geologic Hazards", "count": 1}]
    assert not any(lnk["rel"] == "items"
                   for lnk in stac._subcatalog_doc("ugs-publications", kids)["links"])
    rolled = stac._subcatalog_doc("ugs-serving-topics", kids, items_index=True)
    assert any(lnk["rel"] == "items" and lnk["href"] == "./items.json" for lnk in rolled["links"])


def _related_item() -> dict:
    return {
        "id": "enmin_ucrc_wells",
        "collection": "emp",
        "properties": {"title": "UCRC Wells", "ugs:dbt_schema": "emp"},
        "assets": {"boxes": {
            "href": "https://x/boxes.parquet", "type": "application/vnd.apache.parquet",
            "roles": ["data", "related"], "title": "Boxes",
            "ugs:foreign_keys": [{"fields": ["uwi"], "reference": {"resource": "enmin_ucrc_wells",
                                                                   "fields": ["uwi"]}}],
            "table:columns": [{"name": "uwi"}, {"name": "box_number"}],
        }},
    }


def test_index_entry_self_link_resolves_to_the_item_doc():
    """The index trims assets, so consumers needing join metadata must be able to reach the
    full item. Leaf index sits in the collection dir; the rollup one level above it."""
    leaf = stac._index_entry(_related_item())
    assert {"rel": "self", "href": "./enmin_ucrc_wells/enmin_ucrc_wells.json",
            "type": "application/geo+json"} in leaf["links"]
    rolled = stac._index_entry(_related_item(), rollup=True)
    assert any(lnk["rel"] == "self" and lnk["href"] == "./emp/enmin_ucrc_wells/enmin_ucrc_wells.json"
               for lnk in rolled["links"])


def test_index_entry_assets_stay_summaries():
    """Widening this allowlist is a cost every list view pays. Consumers follow `self` instead —
    reading join metadata off the index is what broke related tables in the viewer
    (UGS-GIO/ugs-map-viewer#491)."""
    asset = stac._index_entry(_related_item())["assets"]["boxes"]
    assert set(asset) == {"href", "type", "roles", "title"}
    assert "ugs:foreign_keys" not in asset and "table:columns" not in asset


def test_index_entry_keeps_web_map_links_alongside_self():
    item = {**_related_item(), "links": [
        {"rel": "self", "href": "./enmin_ucrc_wells.json"},          # item-relative, not reusable
        {"rel": "pmtiles", "href": "https://x/w.pmtiles", "type": "application/vnd.pmtiles",
         "pmtiles:layers": ["enmin_ucrc_wells"]},
    ]}
    links = stac._index_entry(item)["links"]
    assert [lnk["rel"] for lnk in links] == ["self", "pmtiles"]
    # The item's own `self` is relative to its own directory — the index must not copy it through.
    assert links[0]["href"] == "./enmin_ucrc_wells/enmin_ucrc_wells.json"


def _gen_local_catalog():
    """The local-catalog generator inlines core/stac's builders (it can't import core/stac —
    that pulls obstore, absent from the local env), so load it by path."""
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / "viewer" / "scripts" / "gen_local_catalog.py"
    spec = importlib.util.spec_from_file_location("gen_local_catalog", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_local_catalog_index_entries_match_production():
    """A local catalog that carries more than production is a bug the laptop can't reproduce:
    related tables resolved in dev off metadata prod strips, and 2.8.0 shipped without them
    (UGS-GIO/ugs-map-viewer#491). The two builders must agree entry for entry."""
    item = {**_related_item(), "bbox": [-114.0, 37.0, -109.0, 42.0], "links": [
        {"rel": "pmtiles", "href": "https://x/w.pmtiles", "type": "application/vnd.pmtiles"},
    ]}
    local = _gen_local_catalog()
    for rollup in (False, True):
        assert local._index_entry(item, rollup=rollup) == stac._index_entry(item, rollup=rollup)


def _mem_gcs(monkeypatch):
    store: dict[str, bytes] = {}
    monkeypatch.setattr(stac.gcs, "put_bytes", lambda b, p, **k: store.__setitem__(p, b))
    monkeypatch.setattr(stac.gcs, "get_bytes", lambda p: store[p])
    monkeypatch.setattr(stac.gcs, "list_paths", lambda pre: [k for k in store if k.startswith(pre)])
    return store


def test_group_title_only_inherits_pub_type_under_a_pub_catalog():
    """A pub series IS its pub type; anywhere else that field describes the source publication,
    so inheriting it mislabels the group — `geolmap_24k_series` was titled "Open File Report" (#86)."""
    ofr_items = [{"properties": {"ugs:pub_type": "Open File Report"}}]
    assert stac._group_title("ugs-publications", ofr_items) == "Open File Report"
    # Rasters built from OFR plates carry the same property — the group must not take it.
    assert stac._group_title("ugs-rasters", ofr_items) is None
    assert stac._group_title("ugs-serving-topics", ofr_items) is None


def test_group_title_prefers_an_ingest_supplied_collection_title():
    items = [{"properties": {"ugs:pub_type": "Open File Report",
                             stac.COLLECTION_TITLE_PROP: "24k Geologic Map Series"}}]
    assert stac._group_title("ugs-rasters", items) == "24k Geologic Map Series"
    assert stac._group_title("ugs-publications", items) == "24k Geologic Map Series"  # wins over pub type


def test_refresh_catalog_nests_serving_topics_by_schema(monkeypatch):
    """Serving topics split into per-schema collections under a `ugs-serving-topics` sub-catalog,
    with a rollup items.json so one-URL consumers (featureserv, tiles, ops) keep working."""
    import json

    from ugs_warehouse.vector import sink_stac as vec_sink

    store = _mem_gcs(monkeypatch)
    monkeypatch.setattr(stac.config, "EXTERNAL_CATALOGS", [])  # isolate local nesting from federation
    for schema, iid in (("hazards", "hazards_qfaults"), ("emp", "enmin_ucrc_wells")):
        item = stac.build_item(
            item_id=iid, collection=schema, collection_path=vec_sink.collection_path(schema),
            geometry=stac.bbox_polygon([0, 1, 2, 3]), bbox=[0, 1, 2, 3],
            datetime_iso="2026-01-01T00:00:00Z",
            properties={"ugs:dbt_schema": schema}, assets={})
        stac.write_item(item)

    stac.refresh_catalog()
    p = config.STAC_PREFIX

    # Root -> sub-catalog (not a flat collection), carrying both schemas' items.
    root = json.loads(store[f"{p}/catalog.json"])
    kids = [lnk["href"] for lnk in root["links"] if lnk["rel"] == "child"]
    assert kids == ["./ugs-serving-topics/catalog.json"]

    sub = json.loads(store[f"{p}/ugs-serving-topics/catalog.json"])
    assert sub["type"] == "Catalog" and sub["ugs:item_count"] == 2
    assert [lnk["href"] for lnk in sub["links"] if lnk["rel"] == "child"] == [
        "./emp/collection.json", "./hazards/collection.json"]

    # Each schema is a real Collection. Its title is the prettified schema name — nothing is
    # authored for a group with no upstream label — and it points at the Features service root.
    hazards = json.loads(store[f"{p}/ugs-serving-topics/hazards/collection.json"])
    assert hazards["type"] == "Collection" and hazards["id"] == "hazards"
    assert hazards["title"] == "Hazards"
    assert [lnk["href"] for lnk in hazards["links"] if lnk["rel"] == "service"] \
        == [f"{stac.PGF_BASE_URL}/collections"]

    # A nested item's relative links must resolve to real objects — the depth math is off-by-one
    # bait, and a wrong `../` only shows up as a broken catalog in a client, never as an error here.
    import posixpath
    item_obj = f"{p}/ugs-serving-topics/hazards/hazards_qfaults/hazards_qfaults.json"
    item = json.loads(store[item_obj])
    for rel in ("root", "parent", "collection"):
        href = next(lnk["href"] for lnk in item["links"] if lnk["rel"] == rel)
        assert posixpath.normpath(posixpath.join(posixpath.dirname(item_obj), href)) in store, rel

    # Rollup index spans every child collection; per-collection indexes stay scoped.
    rollup = json.loads(store[f"{p}/ugs-serving-topics/items.json"])
    assert {it["id"] for it in rollup["items"]} == {"hazards_qfaults", "enmin_ucrc_wells"}
    scoped = json.loads(store[f"{p}/ugs-serving-topics/hazards/items.json"])
    assert [it["id"] for it in scoped["items"]] == ["hazards_qfaults"]


def test_build_catalog_series_filter():
    from unittest.mock import patch

    from ugs_warehouse.pubs.ingest import build_catalog

    with patch("ugs_warehouse.pubs.source.read_pubs") as mock_read, \
         patch("ugs_warehouse.pubs.source.read_attachments", return_value=[]), \
         patch("ugs_warehouse.pubs.ingest._ids_with_suffix", return_value=set()), \
         patch("ugs_warehouse.pubs.ingest._contents_by_sid", return_value={}), \
         patch("ugs_warehouse.pubs.ingest._threed_classes_by_sid", return_value={}), \
         patch("ugs_warehouse.pubs.ingest._overrides_by_sid", return_value={}), \
         patch("ugs_warehouse.pubs.ingest._build_search_corpus"), \
         patch("ugs_warehouse.pubs.ingest._unit_ids", return_value=set()), \
         patch("ugs_warehouse.pubs.ingest._mirrored_files", return_value=set()), \
         patch("ugs_warehouse.pubs.ingest._cog_footprints", return_value={}), \
         patch("ugs_warehouse.pubs.sink_stac.build_item") as mock_build, \
         patch("ugs_warehouse.core.stac.attach_renders"), \
         patch("ugs_warehouse.core.stac.attach_iso"), \
         patch("ugs_warehouse.core.styles.warm"), \
         patch("ugs_warehouse.core.stac.write_item"), \
         patch("ugs_warehouse.core.stac.refresh_catalog") as mock_refresh:

         mock_read.return_value = [
             {"series_id": "DS-8"},
             {"series_id": "OFR-12"},
             {"series_id": "DS-2"},
         ]

         count = build_catalog(series="DS", skip_refresh=True)

         assert count == 2
         assert mock_build.call_count == 2
         mock_refresh.assert_not_called()


def test_list_series(capsys):
    from unittest.mock import patch

    from ugs_warehouse.pubs.ingest import list_series

    with patch("ugs_warehouse.pubs.source.read_pubs") as mock_read:
         mock_read.return_value = [
             {"series_id": "DS-8"},
             {"series_id": "OFR-12"},
             {"series_id": "DS-2"},
         ]
         rc = list_series()
         assert rc == 0
         captured = capsys.readouterr()
         assert "Discovered series codes:" in captured.out
         assert "DS" in captured.out
         assert "OFR" in captured.out


def test_root_doc_carries_a_title(monkeypatch):
    """Without this a STAC Browser shows the bare id at the top of the tree."""
    monkeypatch.setattr(stac.config, "CATALOG_TITLE", "Utah Geological Survey data warehouse")
    assert stac._root_doc([])["title"] == "Utah Geological Survey data warehouse"


def test_collection_doc_titles_its_item_links():
    """Without titles, listing a collection is an N+1 fetch just to learn item names."""
    doc = stac._collection_doc(
        "hazards", "ugs-serving-topics/hazards", ["qfaults", "landslides"],
        item_titles={"qfaults": "Quaternary Faults"},
    )
    items = {lnk["href"]: lnk for lnk in doc["links"] if lnk["rel"] == "item"}
    assert items["./qfaults/qfaults.json"]["title"] == "Quaternary Faults"
    # A failed fetch has no title: emit the link anyway, without an empty title attribute.
    assert "title" not in items["./landslides/landslides.json"]
