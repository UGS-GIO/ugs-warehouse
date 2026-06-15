"""Shared STAC builders — the collections-layout catalog (derive-from-truth refresh)."""
from ugs_warehouse.core import config, stac


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


def test_cog_link():
    link = stac.cog_link("http://x/a.tif")
    assert link["rel"] == "cog"
    assert "cloud-optimized" in link["type"]


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


def test_root_doc_sorts_child_collections():
    doc = stac._root_doc(["b", "a"])
    assert doc["type"] == "Catalog"
    children = [link["href"] for link in doc["links"] if link["rel"] == "child"]
    assert children == ["./a/collection.json", "./b/collection.json"]


def test_collection_doc_sorts_items_and_has_service_link():
    doc = stac._collection_doc("ugs-publications", ["i2", "i1"])
    assert doc["type"] == "Collection"
    items = [link["href"] for link in doc["links"] if link["rel"] == "item"]
    assert items == ["./i1/i1.json", "./i2/i2.json"]
    assert any(link["rel"] == "service" for link in doc["links"])


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
