"""Shared STAC builders — the collections-layout catalog (derive-from-truth refresh)."""
from ugs_warehouse.core import config, stac
from ugs_warehouse.pubs import identity
from ugs_warehouse.pubs.sink_stac import collection_group


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


def test_root_doc_sorts_child_hrefs():
    doc = stac._root_doc([{"href": "./b/collection.json", "title": "B", "count": 2},
                          {"href": "./a/collection.json", "title": "A", "count": 1}])
    assert doc["type"] == "Catalog"
    kids = [(lnk["href"], lnk.get("title"), lnk.get("ugs:item_count"))
            for lnk in doc["links"] if lnk["rel"] == "child"]
    assert kids == [("./a/collection.json", "A", 1), ("./b/collection.json", "B", 2)]


def test_collection_doc_sorts_items_and_has_service_link():
    doc = stac._collection_doc("ugs-serving-topics", "ugs-serving-topics", ["i2", "i1"])
    assert doc["type"] == "Collection"
    items = [link["href"] for link in doc["links"] if link["rel"] == "item"]
    assert items == ["./i1/i1.json", "./i2/i2.json"]
    assert any(link["rel"] == "service" for link in doc["links"])
    assert any(lnk["rel"] == "root" and lnk["href"] == "../catalog.json" for lnk in doc["links"])


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
         patch("ugs_warehouse.pubs.ingest._footprint_geoms", return_value={}), \
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
