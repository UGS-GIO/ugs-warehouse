"""Shared STAC builders — the collections-layout catalog (derive-from-truth refresh)."""
import json
from unittest.mock import patch

import pytest

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


def test_build_item_declares_versioning_extension_when_versioned():
    item = stac.build_item(
        item_id="M-299DM", collection="M", collection_path="ugs-publications/M",
        geometry=None, bbox=None, datetime_iso="1998-01-01T00:00:00Z",
        properties={"version": "1998", "deprecated": False}, assets={},
    )
    assert stac.VERSION_EXT in item["stac_extensions"]
    assert item["properties"]["version"] == "1998"
    assert item["properties"]["deprecated"] is False


def test_build_item_omits_versioning_extension_when_unversioned():
    item = stac.build_item(
        item_id="M-299DM", collection="M", collection_path="ugs-publications/M",
        geometry=None, bbox=None, datetime_iso=None, properties={}, assets={},
    )
    assert stac.VERSION_EXT not in (item.get("stac_extensions") or [])


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
    assert not any(lnk["rel"] == stac.INDEX_REL
                   for lnk in stac._subcatalog_doc("ugs-publications", kids)["links"])
    rolled = stac._subcatalog_doc("ugs-serving-topics", kids, items_index=True)
    assert any(lnk["rel"] == stac.INDEX_REL and lnk["href"] == "./items.json"
               for lnk in rolled["links"])


def test_index_link_never_uses_the_reserved_items_rel():
    """rel:"items" means an ItemCollection endpoint; ours is a compact custom index, and a
    standard client that follows it there rejects the whole collection."""
    docs = [stac._subcatalog_doc("ugs-serving-topics", [{"id": "hazards", "count": 1}], items_index=True),
            stac._collection_doc("hazards", "ugs-serving-topics/hazards", ["hazards_qfaults"])]
    for doc in docs:
        assert not any(lnk["rel"] == "items" for lnk in doc["links"])


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


def test_local_catalog_collection_service_link_matches_production():
    """gen_local_catalog inlines a simplified `_collection_doc`; the OGC API Features service link
    it writes must still match production's, or the local dev catalog advertises a link that 404s
    (featureserv keys collections by item id, so `/collections/<collection>` never existed) or points
    at a dead host. The whole-doc can differ (simplified), but the service link is the same shape as
    prod: `/collections` root, "OGC API Features service", and present on the SAME nodes
    (flat + serving-topic schemas). Guards the divergence UGS-GIO/ugs-warehouse#289 fixes."""
    local = _gen_local_catalog()

    def service(doc: dict) -> list[dict]:
        return [{k: lk.get(k) for k in ("rel", "href", "type", "title")}
                for lk in doc["links"] if lk["rel"] == "service"]

    for path in ("ugs-geologic-maps", "ugs-serving-topics/hazards", "ugs-publications/DS"):
        cid = path.split("/")[-1]
        assert service(local._collection_doc(path, ["x"])) \
            == service(stac._collection_doc(cid, path, ["x"])), path


def _mem_gcs(monkeypatch):
    store: dict[str, bytes] = {}

    def _upload(local: str, path: str, **k):
        # item_mirror uploads a real file; keep the bytes so a test can read the mirror back.
        with open(local, "rb") as fh:
            body = fh.read()
        store[path] = body
        return stac.gcs.FileMeta(len(body), "1220" + "ee" * 32)

    monkeypatch.setattr(stac.gcs, "upload", _upload)
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
         patch("ugs_warehouse.pubs.ingest._vector_manifests_by_sid", return_value={}), \
         patch("ugs_warehouse.pubs.editions.quad_by_series", return_value={}), \
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


def test_build_catalog_degrades_loudly_when_footprints_parquet_is_missing(capsys):
    """ALL-5954 Clinton review, FIX 1 (blocking): editions are an enhancement — a missing/unreadable
    footprints parquet must not abort the whole pub catalog rebuild. `editions.edition_graph`
    raising RuntimeError must be caught, warned loudly on stderr, and the build must still complete
    with every item carrying no edition info (edition=None), not blow up the run."""
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
         patch("ugs_warehouse.pubs.ingest._vector_manifests_by_sid", return_value={}), \
         patch("ugs_warehouse.pubs.editions.edition_graph",
               side_effect=RuntimeError("footprints missing")), \
         patch("ugs_warehouse.pubs.sink_stac.build_item") as mock_build, \
         patch("ugs_warehouse.core.stac.attach_renders"), \
         patch("ugs_warehouse.core.stac.attach_iso"), \
         patch("ugs_warehouse.core.styles.warm"), \
         patch("ugs_warehouse.core.stac.write_item"), \
         patch("ugs_warehouse.core.stac.refresh_catalog"):

         mock_read.return_value = [
             {"series_id": "DS-8"},
             {"series_id": "OFR-12"},
         ]

         count = build_catalog(skip_refresh=True)

         assert count == 2
         assert mock_build.call_count == 2
         assert all(c.kwargs["edition"] is None for c in mock_build.call_args_list)

         err = capsys.readouterr().err
         assert "WARNING" in err and "edition detection skipped" in err and "footprints missing" in err


def test_vector_manifests_by_sid_reads_the_authoritative_split():
    """_vector_manifests_by_sid() must read pubs/vectors.py's per-series `_manifest.json` — the
    AUTHORITATIVE spatial-vs-table split — rather than guess spatial/table from label names."""
    from unittest.mock import patch

    from ugs_warehouse.pubs import vectors
    from ugs_warehouse.pubs.ingest import _vector_manifests_by_sid

    manifest = {"spatial": ["geo__ContactsAndFaults"], "tables": ["geo__DescriptionOfMapUnits"]}
    paths = [
        f"{vectors.VECTORS_PREFIX}/M-100/geo__ContactsAndFaults.parquet",
        f"{vectors.VECTORS_PREFIX}/M-100/geo__DescriptionOfMapUnits.parquet",
        f"{vectors.VECTORS_PREFIX}/M-100/_manifest.json",
        f"{vectors.VECTORS_PREFIX}/DS-2/_manifest.json",  # unreadable — must not blow up the rest
    ]
    bodies = {f"{vectors.VECTORS_PREFIX}/M-100/_manifest.json": json.dumps(manifest).encode()}

    def get_bytes(path):
        if path not in bodies:
            raise FileNotFoundError(path)
        return bodies[path]

    with patch("ugs_warehouse.pubs.ingest.gcs.list_paths", return_value=paths), \
         patch("ugs_warehouse.pubs.ingest.gcs.get_bytes", side_effect=get_bytes):
        out = _vector_manifests_by_sid()

    assert out["M-100"] == manifest  # keyed by uppercased series_id, matching the other discovery maps
    assert "DS-2" not in out


def test_vector_manifests_by_sid_skips_a_non_object_manifest(capsys):
    """A manifest that is valid JSON but not an object (`null`, a list, a bare string — a partial
    or corrupt write) must not crash discovery for every other pub. `.get()` on a non-dict raises
    AttributeError; that has to land inside the same try/except as the read, or one bad manifest
    takes down the whole build_catalog run instead of just costing its own pub the vector assets.

    The swallow must still be visible, though: ALL-5913 final-review fail-loud fix wants the bad
    manifest's path named on stderr so a corrupt (vs merely absent) manifest isn't a silent no-op.
    """
    from unittest.mock import patch

    from ugs_warehouse.pubs import vectors
    from ugs_warehouse.pubs.ingest import _vector_manifests_by_sid

    good = {"spatial": ["geo__ContactsAndFaults"], "tables": []}
    bad_path = f"{vectors.VECTORS_PREFIX}/DS-9/_manifest.json"
    paths = [f"{vectors.VECTORS_PREFIX}/M-100/_manifest.json", bad_path]
    bodies = {
        f"{vectors.VECTORS_PREFIX}/M-100/_manifest.json": json.dumps(good).encode(),
        bad_path: b"null",
    }

    with patch("ugs_warehouse.pubs.ingest.gcs.list_paths", return_value=paths), \
         patch("ugs_warehouse.pubs.ingest.gcs.get_bytes", side_effect=lambda p: bodies[p]):
        out = _vector_manifests_by_sid()  # must return, not raise

    assert out["M-100"] == good  # unaffected by the sibling's bad manifest
    assert "DS-9" not in out  # skipped, not crashed on
    assert bad_path in capsys.readouterr().err  # but named on stderr, not silently dropped


def test_build_catalog_wires_vector_layers_and_companion_tables():
    """ALL-5913 task 3: build_catalog must discover each pub's extracted vector layers + companion
    tables (per pubs/vectors.py's manifest) and pass them into build_item — otherwise the assets
    task 2 wired up in build_item never actually get attached to a real item."""
    from unittest.mock import patch

    from ugs_warehouse.pubs.ingest import build_catalog

    manifests = {"DS-8": {"spatial": ["geo__ContactsAndFaults"],
                          "tables": ["geo__DescriptionOfMapUnits"]}}

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
         patch("ugs_warehouse.pubs.ingest._vector_manifests_by_sid", return_value=manifests), \
         patch("ugs_warehouse.pubs.editions.quad_by_series", return_value={}), \
         patch("ugs_warehouse.pubs.sink_stac.build_item") as mock_build, \
         patch("ugs_warehouse.core.stac.attach_renders"), \
         patch("ugs_warehouse.core.stac.attach_iso"), \
         patch("ugs_warehouse.core.styles.warm"), \
         patch("ugs_warehouse.core.stac.write_item"), \
         patch("ugs_warehouse.core.stac.refresh_catalog"):

         mock_read.return_value = [
             {"series_id": "DS-8"},
             {"series_id": "OFR-12"},
         ]

         count = build_catalog(skip_refresh=True)

         assert count == 2
         by_sid = {c.args[0]["series_id"]: c.kwargs for c in mock_build.call_args_list}
         assert by_sid["DS-8"]["vector_layers"] == ["geo__ContactsAndFaults"]
         assert by_sid["DS-8"]["companion_tables"] == [
             {"label": "geo__DescriptionOfMapUnits", "columns": None}]
         # a series absent from the manifest map still gets empty lists, never None/KeyError.
         assert by_sid["OFR-12"]["vector_layers"] == []
         assert by_sid["OFR-12"]["companion_tables"] == []


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


def test_pub_keywords_are_a_list_and_reach_the_iso_record():
    """`keywords` is a list in STAC. The source hands over one `;`-separated blob, and publishing
    that string made core/iso.py iterate it per character: every pub's ISO record carried a
    <gmd:keyword> for each letter (the same fault #64 fixed on the vector path)."""
    raw = "Geology; Summit County; Maps\nGeology; Tooele, Utah; Maps"
    with patch("ugs_warehouse.core.stac.prior_property", return_value=""), \
         patch("ugs_warehouse.core.stac.manual_override", return_value={}):
        item = pubs_sink.build_item({"series_id": "DS-8", "pub_name": "Test Pub", "series": "DS",
                                     "keywords": raw}, [])

    # Deduped, and a comma inside a heading stays inside it.
    assert item["properties"]["keywords"] == ["Geology", "Summit County", "Maps", "Tooele, Utah"]
    record = iso.stac_to_iso19139(item)
    assert "<gmd:keyword><gco:CharacterString>Summit County</gco:CharacterString></gmd:keyword>" in record
    assert "<gco:CharacterString>G</gco:CharacterString>" not in record


def test_pub_item_omits_the_fields_the_source_left_empty():
    """STAC requires a non-empty description, and an empty string says nothing an absent key does
    not. A pub with no citation and no subjects must carry neither field."""
    with patch("ugs_warehouse.core.stac.prior_property", return_value=""), \
         patch("ugs_warehouse.core.stac.manual_override", return_value={}):
        item = pubs_sink.build_item({"series_id": "DS-8", "pub_name": "Test Pub", "series": "DS",
                                     "keywords": "  ; \n", "full_citation": ""}, [])

    props = item["properties"]
    for absent in ("description", "keywords", "ugs:scale", "ugs:author"):
        assert absent not in props, absent
    assert props["ugs:series"] == "DS"   # a value the source did give still lands


def test_pub_item_emits_edition_version_and_supersession_links():
    edition = {
        "version": "2005", "deprecated": True,
        "predecessor_href": None,
        "successor_href": "https://maps-assets.geology.utah.gov/warehouse/stac/"
                          "ugs-publications/M/M-233/M-233.json",
    }
    with patch("ugs_warehouse.core.stac.prior_property", return_value=""), \
         patch("ugs_warehouse.core.stac.manual_override", return_value={}):
        item = pubs_sink.build_item(
            {"series_id": "M-159", "pub_name": "Old Ed", "series": "M", "pub_year": "1980"},
            [], edition=edition,
        )
    assert item["properties"]["version"] == "2005"
    assert item["properties"]["deprecated"] is True
    assert stac.VERSION_EXT in item["stac_extensions"]
    succ = [lnk for lnk in item["links"] if lnk["rel"] == "successor-version"]
    assert succ and succ[0]["href"].endswith("/M-233/M-233.json")
    assert not [lnk for lnk in item["links"] if lnk["rel"] == "predecessor-version"]


def _build(p, **kw):
    with patch("ugs_warehouse.core.stac.prior_property", return_value=""), \
         patch("ugs_warehouse.core.stac.manual_override", return_value={}):
        return pubs_sink.build_item(p, [], **kw)


def test_cog_map_links_to_its_scale_tier_mosaic():
    # The tier is the mosaic's identity, not pub metadata: a COG map carries no `ugs:scale_tier`,
    # only its raw `ugs:scale`, and reaches its tier via a `rel:related` link to the mosaic.
    item = _build({"series_id": "GQ-968", "series": "GQ", "pub_scale": "1:24,000"}, has_cog=True)
    assert "ugs:scale_tier" not in item["properties"]
    assert item["properties"]["ugs:scale"] == "1:24,000"   # raw scale stays as per-item metadata
    rel = [lnk for lnk in item["links"] if lnk["rel"] == "related"]
    assert len(rel) == 1
    assert rel[0]["href"].endswith("/ugs-geologic-maps/geologic-maps-24k/geologic-maps-24k.json")

    # COG map, UNPARSEABLE scale: still linked to the default tier (mirrors _group_by_tier's fallback).
    item = _build({"series_id": "M-1", "series": "M", "pub_scale": "n/a"}, has_cog=True)
    rel = [lnk for lnk in item["links"] if lnk["rel"] == "related"]
    assert len(rel) == 1 and rel[0]["href"].endswith("/geologic-maps-24k/geologic-maps-24k.json")

    # non-COG pub: not stitched into any mosaic, so no member link.
    item = _build({"series_id": "OFR-5", "series": "OFR", "pub_scale": "1:500,000"}, has_cog=False)
    assert not [lnk for lnk in item["links"] if lnk["rel"] == "related"]


def test_deprecated_edition_drops_the_mosaic_related_link():
    """A deprecated edition (superseded per the quad edition graph, e.g. GQ-852 superseded by
    M-296DM) must NOT carry a `related` link into the mosaic — the `--editions current` default
    drops superseded editions before stitching, so the link would otherwise claim membership in a
    mosaic whose own `derived_from` omits it (catalog self-contradiction, ALL-5954 final review)."""
    item = _build({"series_id": "GQ-852", "series": "GQ", "pub_scale": "1:24,000"},
                  has_cog=True, edition={"deprecated": True, "version": "1971"})
    assert not [lnk for lnk in item["links"]
               if lnk["rel"] == "related" and "geologic-maps-" in lnk["href"]]

    # no edition info at all -> not known to be superseded -> still current -> keeps the link.
    item = _build({"series_id": "M-296DM", "series": "M", "pub_scale": "1:24,000"},
                  has_cog=True, edition=None)
    rel = [lnk for lnk in item["links"]
          if lnk["rel"] == "related" and "geologic-maps-" in lnk["href"]]
    assert len(rel) == 1

    # explicitly current (not deprecated) -> keeps the link too.
    item = _build({"series_id": "M-296DM", "series": "M", "pub_scale": "1:24,000"},
                  has_cog=True, edition={"deprecated": False, "version": "2022"})
    rel = [lnk for lnk in item["links"]
          if lnk["rel"] == "related" and "geologic-maps-" in lnk["href"]]
    assert len(rel) == 1


def test_pub_item_id_is_safe_in_a_path():
    """The id is a directory name and the tail of every link to the item, so a space in it made
    the item's own self link, its collection's item link and the ISO href unresolvable."""
    assert pubs_sink.item_id_for("Geologic map of Utah") == "Geologic-map-of-Utah"
    assert pubs_sink.item_id_for("557.92 UT1CO") == "557.92-UT1CO"
    # Already safe ids are untouched, trailing punctuation included: `~` and `-` are unreserved.
    assert pubs_sink.item_id_for("OF-70-234~2") == "OF-70-234~2"
    assert pubs_sink.item_id_for("PI-") == "PI-"

    with patch("ugs_warehouse.core.stac.prior_property", return_value=""), \
         patch("ugs_warehouse.core.stac.manual_override", return_value={}):
        item = pubs_sink.build_item({"series_id": "Geologic map of Utah", "series": "GEOLOGIC"}, [])

    assert item["id"] == "Geologic-map-of-Utah"
    assert item["properties"]["ugs:series_id"] == "Geologic map of Utah"  # the source string survives
    assert all(" " not in lk["href"] for lk in item["links"])


def test_pub_href_drops_undefined_and_upgrades_ugspub_to_https():
    """`undefined` reached the database as text and resolved to an asset that 404s by
    construction. ugspub answers 303 to https, and a page served over https cannot fetch a
    plaintext asset."""
    assert pubs_sink.href("undefined") is None
    assert pubs_sink.href("  ") is None
    assert pubs_sink.href("http://ugspub.nr.utah.gov/publications/ofr/OFR-1.pdf") \
        == "https://ugspub.nr.utah.gov/publications/ofr/OFR-1.pdf"
    # Any host we run, not just ugspub — a UGS web app was still published over http.
    assert pubs_sink.href("http://geology.utah.gov/apps/tour/index.html") \
        == "https://geology.utah.gov/apps/tour/index.html"
    # A third-party host is left alone: assuming TLS somewhere we do not operate can break a link
    # that works. `wp.me` is the live example, and it is 404 on both schemes anyway.
    assert pubs_sink.href("http://wp.me/P5HpmR-1ys") == "http://wp.me/P5HpmR-1ys"
    # A bare filename still resolves against the publications host, and other hosts are left alone.
    assert pubs_sink.href("ofr/OFR-1.pdf") == pubs_sink.UGSPUB + "ofr/OFR-1.pdf"
    assert pubs_sink.href("https://example.org/x.pdf") == "https://example.org/x.pdf"


def test_pub_media_types_name_the_format():
    """An unmapped extension falls back to application/octet-stream, which tells a client nothing.
    353 mining-district scans and every spreadsheet were published that way."""
    assert pubs_sink.media_type("https://ugspub.nr.utah.gov/publications/uranium_data/MD01037.tif") == "image/tiff"
    assert pubs_sink.media_type("x/y.kmz") == "application/vnd.google-earth.kmz"
    assert pubs_sink.media_type("x/y.htm") == "text/html"
    # .xlsx claimed the .xls type, which is a different format with a different reader.
    assert pubs_sink.media_type("x/y.xlsx") == \
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert pubs_sink.media_type("x/y.xls") == "application/vnd.ms-excel"
    # A query string never made it into the extension, and an unknown one still falls back.
    assert pubs_sink.media_type("x/y.pdf?v=2") == "application/pdf"
    assert pubs_sink.media_type("https://www.youtube.com/watch?v=abc") == "application/octet-stream"


def test_cog_assets_keep_the_cloud_optimized_media_type():
    """`.tif` maps to image/tiff for the publisher's scans. A COG we produced sets its type on the
    asset directly, so the mapping must not reach it — the profile parameter is what a client reads
    to know it can range-request the file."""
    with patch("ugs_warehouse.core.stac.prior_property", return_value=""), \
         patch("ugs_warehouse.core.stac.manual_override", return_value={}):
        item = pubs_sink.build_item({"series_id": "M-100", "series": "M"}, [], has_cog=True)

    assert item["assets"]["cog"]["type"] == pubs_sink.COG_MIME
    assert "cloud-optimized" in item["assets"]["cog"]["type"]


def test_pub_item_exposes_all_vector_layers_and_companion_tables():
    """pubs/vectors.py extracts every GDB layer and GeMS companion table to Parquet on GCS —
    ALL-5913 task 2 wants each one surfaced as its own STAC asset, not just the single hardcoded
    `units` layer, so a client can discover and fetch any extracted layer/table by name."""
    with patch("ugs_warehouse.core.stac.prior_property", return_value=""), \
         patch("ugs_warehouse.core.stac.manual_override", return_value={}):
        item = pubs_sink.build_item(
            {"series_id": "M-100", "series": "M"}, [],
            vector_layers=["gems__ContactsAndFaults", "gems__MapUnitPolys"],
            companion_tables=[{"label": "gems__DescriptionOfMapUnits",
                               "columns": [{"name": "MapUnit"}, {"name": "Age"}]}],
        )
    a = item["assets"]
    assert a["gems__ContactsAndFaults"]["href"].endswith("geolmap/vectors/M-100/gems__ContactsAndFaults.parquet")
    assert a["gems__ContactsAndFaults"]["type"] == pubs_sink.PARQUET_MIME
    assert a["gems__ContactsAndFaults"]["roles"] == ["data"]
    assert "gems__MapUnitPolys" in a  # the 2nd vector_layers entry produced an asset too
    assert a["gems__DescriptionOfMapUnits"]["href"].endswith(
        "geolmap/vectors/M-100/gems__DescriptionOfMapUnits.parquet")
    assert a["gems__DescriptionOfMapUnits"]["type"] == pubs_sink.PARQUET_MIME
    assert a["gems__DescriptionOfMapUnits"]["roles"] == ["data"]
    assert a["gems__DescriptionOfMapUnits"]["table:columns"] == [{"name": "MapUnit"}, {"name": "Age"}]
    assert pubs_sink.stac.TABLE_EXT in item["stac_extensions"]


def test_vector_layer_label_colliding_with_a_reserved_key_keeps_both_assets(capsys):
    """ALL-5913 final-review must-fix: a bare shapefile can produce a label equal to a reserved
    asset key — `units.shp` -> `units` would otherwise silently overwrite the canonical `units`
    GeoParquet asset (`assets[label] = ...` clobbers in place, no trace). Both must survive under
    distinct keys, and the collision must be visible on stderr rather than a link just vanishing.
    """
    with patch("ugs_warehouse.core.stac.prior_property", return_value=""), \
         patch("ugs_warehouse.core.stac.manual_override", return_value={}):
        item = pubs_sink.build_item(
            {"series_id": "M-100", "series": "M"}, [],
            has_units=True, vector_layers=["units"],
        )
    a = item["assets"]
    # the canonical units GeoParquet asset is untouched
    assert a["units"]["href"].endswith("geolmap/units/M-100/M-100.units.parquet")
    assert a["units"]["title"] == "Geologic unit polygons (GeoParquet)"
    # the extracted vector layer survives under a distinct key instead of being dropped
    assert a["vector_units"]["href"].endswith("geolmap/vectors/M-100/units.parquet")
    assert a["vector_units"]["type"] == pubs_sink.PARQUET_MIME
    err = capsys.readouterr().err
    assert "units" in err  # the collision was named on stderr, not silent


def test_duplicate_vector_layer_labels_keep_both_assets(capsys):
    """Two shapefiles sharing a basename (different sub-folders of the same GIS bundle) produce
    the same extracted label twice. The second must not clobber the first — both assets survive
    under distinct keys, even though (a vectors.py-side concern, out of scope here) they currently
    point at the same object path."""
    with patch("ugs_warehouse.core.stac.prior_property", return_value=""), \
         patch("ugs_warehouse.core.stac.manual_override", return_value={}):
        item = pubs_sink.build_item(
            {"series_id": "M-100", "series": "M"}, [],
            vector_layers=["roads", "roads"],
        )
    a = item["assets"]
    assert a["roads"]["href"].endswith("geolmap/vectors/M-100/roads.parquet")
    assert a["vector_roads"]["href"].endswith("geolmap/vectors/M-100/roads.parquet")
    err = capsys.readouterr().err
    assert "roads" in err


def test_raster_collection_borrows_its_newest_scene_thumbnail(monkeypatch):
    """PTL-VIZ-001 wants a thumbnail on a geospatial collection. A raster collection's items are
    scenes of one dataset, so a scene's preview represents it; the newest one, so the preview
    tracks what was published last rather than whichever id sorts first."""
    store = _mem_gcs(monkeypatch)
    monkeypatch.setattr(stac.config, "EXTERNAL_CATALOGS", [])
    for iid, dt in (("browns_hole_2023", "2023-01-01T00:00:00Z"),
                    ("fort_douglas_2024", "2024-01-01T00:00:00Z")):
        stac.write_item(stac.build_item(
            item_id=iid, collection="geolmap_24k_series",
            collection_path="ugs-rasters/geolmap_24k_series",
            geometry=stac.bbox_polygon([0, 1, 2, 3]), bbox=[0, 1, 2, 3], datetime_iso=dt,
            properties={"title": f"Map {iid}"},
            assets={"cog": {"href": f"https://x/{iid}.tif", "type": config.COG_MIME,
                            "roles": ["data", "visual"]},
                    "thumbnail": {"href": f"https://x/{iid}.png", "type": "image/png",
                                  "roles": ["thumbnail"]}}))
    stac.refresh_catalog()

    coll = json.loads(store[f"{config.STAC_PREFIX}/ugs-rasters/geolmap_24k_series/collection.json"])
    assert coll["assets"]["thumbnail"]["href"] == "https://x/fort_douglas_2024.png"
    assert coll["assets"]["thumbnail"]["roles"] == ["thumbnail"]
    # Named for the scene it came from, so nobody mistakes it for a rendering of the whole series.
    assert coll["assets"]["thumbnail"]["title"] == "Preview: Map fort_douglas_2024"


def test_a_serving_topic_collection_borrows_no_thumbnail(monkeypatch):
    """A dbt schema holds unrelated layers. One layer's preview would misrepresent the rest, so the
    rule stops at raster collections — see #257."""
    store = _mem_gcs(monkeypatch)
    monkeypatch.setattr(stac.config, "EXTERNAL_CATALOGS", [])
    stac.write_item(stac.build_item(
        item_id="hazards_qfaults", collection="hazards",
        collection_path="ugs-serving-topics/hazards",
        geometry=stac.bbox_polygon([0, 1, 2, 3]), bbox=[0, 1, 2, 3],
        datetime_iso="2026-01-01T00:00:00Z", properties={"title": "Quaternary Faults"},
        assets={"thumbnail": {"href": "https://x/q.png", "type": "image/png",
                              "roles": ["thumbnail"]}}))
    stac.refresh_catalog()

    coll = json.loads(store[f"{config.STAC_PREFIX}/ugs-serving-topics/hazards/collection.json"])
    assert "assets" not in coll


def test_a_raster_collection_with_no_scene_thumbnails_omits_the_key(monkeypatch):
    store = _mem_gcs(monkeypatch)
    monkeypatch.setattr(stac.config, "EXTERNAL_CATALOGS", [])
    stac.write_item(stac.build_item(
        item_id="bare_scene", collection="slope", collection_path="ugs-rasters/slope",
        geometry=stac.bbox_polygon([0, 1, 2, 3]), bbox=[0, 1, 2, 3],
        datetime_iso="2026-01-01T00:00:00Z", properties={},
        assets={"cog": {"href": "https://x/b.tif", "type": config.COG_MIME, "roles": ["data"]}}))
    stac.refresh_catalog()

    coll = json.loads(store[f"{config.STAC_PREFIX}/ugs-rasters/slope/collection.json"])
    # No scene thumbnail to borrow, but the mirror is derived from the items themselves.
    assert "thumbnail" not in coll["assets"]
    assert coll["assets"]["items"]["roles"] == ["collection-mirror"]


def test_raster_collection_publishes_an_item_mirror(monkeypatch):
    """PTL-MIR-001. One range request answers what one HTTP fetch per scene answered before, and
    the mirror is rebuilt from the same items the collection doc is, so the two cannot drift."""
    duckdb = pytest.importorskip("duckdb")
    try:
        con = duckdb.connect()
        con.execute("INSTALL spatial; LOAD spatial;")
        con.close()
    except duckdb.Error as e:
        pytest.skip(f"spatial extension unavailable: {e}")

    store = _mem_gcs(monkeypatch)
    monkeypatch.setattr(stac.config, "EXTERNAL_CATALOGS", [])
    for iid, dt, bbox in (("scene_a", "2023-01-01T00:00:00Z", [-114.0, 37.0, -113.0, 38.0]),
                          ("scene_b", "2024-01-01T00:00:00Z", [-112.0, 40.0, -111.0, 41.0])):
        stac.write_item(stac.build_item(
            item_id=iid, collection="geolmap_24k_series",
            collection_path="ugs-rasters/geolmap_24k_series",
            geometry=stac.bbox_polygon(bbox), bbox=bbox, datetime_iso=dt,
            properties={"title": iid.title()},
            assets={"cog": {"href": f"https://x/{iid}.tif", "type": config.COG_MIME,
                            "roles": ["data", "visual"]}}))
    stac.refresh_catalog()

    path = f"{config.STAC_PREFIX}/ugs-rasters/geolmap_24k_series/items.parquet"
    assert path in store, "no items.parquet written"

    coll = json.loads(store[f"{config.STAC_PREFIX}/ugs-rasters/geolmap_24k_series/collection.json"])
    mirror = coll["assets"]["items"]
    assert mirror["type"] == config.PARQUET_MIME
    assert mirror["roles"] == ["collection-mirror"]
    assert mirror["href"].endswith("/ugs-rasters/geolmap_24k_series/items.parquet")
    # The registration is the whole requirement; the spec defines no rel:"items" link for it.
    assert mirror["file:size"] > 0

    # One row per item, carrying the fields a client filters on.
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        local = f"{tmp}/items.parquet"
        with open(local, "wb") as fh:
            fh.write(store[path])
        con = duckdb.connect()
        con.execute("LOAD spatial;")
        rows = con.execute(
            f"SELECT id, bbox.xmin, properties.datetime, ST_GeometryType(geometry) FROM '{local}' ORDER BY id"
        ).fetchall()
        con.close()

    assert [r[0] for r in rows] == ["scene_a", "scene_b"]
    assert rows[0][1] == -114.0                    # bbox struct, not the raw array
    assert rows[0][3] == "POLYGON"                 # geometry hydrated, queryable


def test_a_mirror_is_not_written_for_items_without_geometry(monkeypatch):
    """A row that cannot be queried spatially is worse than an absent one."""
    from ugs_warehouse.core import item_mirror

    aspatial = [{"id": "x", "collection": "c", "geometry": None, "properties": {}}]
    assert item_mirror.write("ugs-rasters/none", aspatial) is None
    assert item_mirror.asset("ugs-rasters/none", None) == {}


def test_every_node_gets_a_readme_and_agents_file(monkeypatch):
    """PTL-FIL-001/002/003: both files beside every catalog and collection, and linked from the
    JSON. The link and the file are written by the same refresh — a link without its file is a
    broken link, which is what `refresh_catalog` rebuilding the links from scratch would cause."""
    store = _mem_gcs(monkeypatch)
    monkeypatch.setattr(stac.config, "EXTERNAL_CATALOGS", [])
    stac.write_item(stac.build_item(
        item_id="DS-8", collection="DS", collection_path="ugs-publications/DS",
        geometry=stac.bbox_polygon([0, 1, 2, 3]), bbox=[0, 1, 2, 3],
        datetime_iso="2026-01-01T00:00:00Z", properties={"title": "A publication"},
        assets={"data": {"href": "https://x/a.parquet", "type": config.PARQUET_MIME,
                         "roles": ["data"]}}))
    stac.refresh_catalog()
    p = config.STAC_PREFIX

    for node in ("", "/ugs-publications", "/ugs-publications/DS"):
        assert f"{p}{node}/README.md" in store, node
        assert f"{p}{node}/AGENTS.md" in store, node

    coll = json.loads(store[f"{p}/ugs-publications/DS/collection.json"])
    rels = {lk["rel"]: lk for lk in coll["links"]}
    assert rels["describedby"]["href"] == "./README.md"
    assert rels["describedby"]["type"] == "text/markdown"
    assert rels["agents"]["href"] == "./AGENTS.md"


def test_the_readme_carries_what_the_rule_asks_for(monkeypatch):
    """PTL-FIL-004/005: a title heading, and the license and provenance in the prose rather than
    only in the JSON."""
    store = _mem_gcs(monkeypatch)
    monkeypatch.setattr(stac.config, "EXTERNAL_CATALOGS", [])
    stac.write_item(stac.build_item(
        item_id="t", collection="hazards", collection_path="ugs-serving-topics/hazards",
        geometry=stac.bbox_polygon([-114, 37, -109, 42]), bbox=[-114, 37, -109, 42],
        datetime_iso="2026-01-01T00:00:00Z", properties={"title": "Quaternary Faults"},
        assets={"pmtiles": {"href": "https://x/t.pmtiles", "type": config.PMTILES_MIME,
                            "roles": ["visual"]}}))
    stac.refresh_catalog()

    md = store[f"{config.STAC_PREFIX}/ugs-serving-topics/hazards/README.md"].decode()
    assert md.startswith("# ")
    assert config.DATA_LICENSE in md
    assert "Utah Geological Survey" in md
    # Derived from the assets the items actually carry, so the advice cannot describe a format
    # this collection does not publish.
    assert "PMTiles" in md and "GeoParquet" not in md
