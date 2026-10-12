import pytest

from ugs_warehouse.core import stac
from ugs_warehouse.pubs import sink_stac


@pytest.fixture(autouse=True)
def _no_gcs(monkeypatch):
    """The description fallback reads the prior published value from GCS; unit tests stay offline."""
    monkeypatch.setattr(stac, "prior_collection_field", lambda *a, **k: None)
    monkeypatch.setattr(stac, "prior_property", lambda *a, **k: None)


FOOT = {"type": "Polygon", "coordinates": [[[-112.5, 40.5], [-112.0, 40.5], [-112.0, 41.0],
                                             [-112.5, 41.0], [-112.5, 40.5]]]}
P = {"series_id": "OFR-784DM", "pub_name": "Interim Geologic Map of Mills Junction",
     "pub_year": "2026", "pub_url": "https://ugspub.nr.utah.gov/publications/ofr/ofr-784dm.pdf",
     "pub_publisher": "Utah Geological Survey", "series": "Open File Report", "pub_scale": "1:24,000",
     "full_citation": "Utah Geological Survey, 2026, Interim Geologic Map of Mills Junction: OFR-784DM."}
ATT = [{"pub_url": "https://ugspub.nr.utah.gov/publications/ofr/ofr-784dm.zip", "extra_data": "GIS Data - Zip"}]


def _build(**kw):
    args = dict(geom=FOOT, bbox=[-112.5, 40.5, -112.0, 41.0], fp_source="cog", has_cog=True,
                has_cover=True, vector_layers=["geo__Faults"],
                companion_tables=[{"label": "geo__DMU", "columns": [{"name": "MapUnit", "type": "string"}]}],
                layers={"geo__Faults": {"rows": 12, "bbox": [-112.4, 40.6, -112.1, 40.9],
                                        "geometry_types": ["LineString"], "proj_code": "EPSG:26912",
                                        "proj_wkt2": "PROJCRS[\"NAD83 / UTM zone 12N\"]"},
                        "geo__DMU": {"rows": 41, "bbox": None, "geometry_types": []}},
                mosaic_tier="24k")
    return sink_stac.build_publication(P, ATT, **{**args, **kw})


def test_documents_sit_on_the_collection_and_data_are_items():
    coll, items = _build()
    assert coll["type"] == "Collection" and coll["id"] == "OFR-784DM"
    assert coll["_collection_path"] == "ugs-publications/OFR/OFR-784DM"
    assert set(coll["assets"]) == {"publication", "gis_data_zip", "thumbnail"}
    assert coll["assets"]["gis_data_zip"]["roles"] == ["source"]
    ids = {it["id"] for it in items}
    assert ids == {"OFR-784DM_plate", "OFR-784DM_geo__Faults", "OFR-784DM_geo__DMU"}
    assert {lk["href"] for lk in coll["links"] if lk["rel"] == "item"} == {f"./{i}/{i}.json" for i in ids}


def test_items_live_under_the_publication_collection():
    coll, items = _build()
    for it in items:
        assert it["collection"] == "OFR-784DM"
        assert it["_collection_path"] == "ugs-publications/OFR/OFR-784DM"
        rels = {lk["rel"]: lk["href"] for lk in it["links"]}
        assert rels["collection"] == "../collection.json" and rels["root"] == "../../../../catalog.json"
        assert rels["cite-as"] == "https://doi.org/10.34191/OFR-784DM"
        assert it["properties"]["sci:doi"] == "10.34191/OFR-784DM" and stac.SCI_EXT in it["stac_extensions"]
        assert it["geometry"] and it["bbox"]
    assert coll["sci:doi"] == "10.34191/OFR-784DM" and coll["sci:citation"]
    assert stac.SCI_EXT in coll["stac_extensions"]


def test_each_item_states_its_own_data_crs_and_never_borrows_4326():
    _, items = _build(cog_fields={"proj:code": "EPSG:3857"})
    by_id = {it["id"]: it for it in items}
    assert by_id["OFR-784DM_geo__Faults"]["properties"]["proj:code"] == "EPSG:26912"
    assert "proj:code" not in by_id["OFR-784DM_geo__DMU"]["properties"]      # a table has no CRS
    assert "proj:code" not in by_id["OFR-784DM_plate"]["properties"]         # the COG asset carries 3857
    assert by_id["OFR-784DM_plate"]["assets"]["cog"]["proj:code"] == "EPSG:3857"
    assert stac.PROJ_EXT in by_id["OFR-784DM_plate"]["stac_extensions"]


def test_a_layer_whose_crs_has_no_epsg_code_carries_wkt2():
    _, items = _build(layers={"geo__Faults": {"rows": 1, "bbox": [-112.4, 40.6, -112.1, 40.9],
                                              "geometry_types": ["LineString"], "proj_code": None,
                                              "proj_wkt2": "PROJCRS[\"Clarke_1866_UTM_Zone_12N\"]"}})
    faults = next(it for it in items if it["id"].endswith("geo__Faults"))
    assert faults["properties"]["proj:wkt2"].startswith("PROJCRS") and "proj:code" not in faults["properties"]


def test_plate_carries_the_cog_and_the_mosaic_link():
    _, items = _build()
    plate = next(it for it in items if it["id"] == "OFR-784DM_plate")
    assert "cog" in plate["assets"]
    assert any(lk["rel"] == "related" and "geologic-maps-24k" in lk["href"] for lk in plate["links"])


def test_layer_uses_its_own_bbox_and_table_uses_the_footprint():
    _, items = _build()
    by_id = {it["id"]: it for it in items}
    assert by_id["OFR-784DM_geo__Faults"]["bbox"] == [-112.4, 40.6, -112.1, 40.9]
    assert by_id["OFR-784DM_geo__Faults"]["properties"]["table:row_count"] == 12
    assert by_id["OFR-784DM_geo__DMU"]["properties"]["table:row_count"] == 41
    table = by_id["OFR-784DM_geo__DMU"]
    assert table["bbox"] == [-112.5, 40.5, -112.0, 41.0]
    assert table["assets"]["data"]["table:columns"] == [{"name": "MapUnit", "type": "string"}]
    assert stac.TABLE_EXT in table["stac_extensions"]


def test_a_report_with_no_data_is_a_collection_with_no_items():
    coll, items = sink_stac.build_publication(
        {**P, "series_id": "C-139", "pub_name": "Utah mining 2024"}, [], has_cover=True)
    assert items == [] and "publication" in coll["assets"]
    assert not [lk for lk in coll["links"] if lk["rel"] == "item"]
    assert coll["extent"]["spatial"]["bbox"] == [stac.UTAH_BBOX]


# Review Focus cases
def test_a_layer_without_a_bbox_in_a_pub_without_a_footprint_gets_the_utah_aoi():
    _, items = _build(geom=None, bbox=None, has_cog=False,
                      layers={"geo__Faults": {"rows": 3, "bbox": None, "geometry_types": []}})
    faults = next(it for it in items if it["id"].endswith("geo__Faults"))
    assert faults["bbox"] == stac.UTAH_BBOX and faults["geometry"]["type"] == "Polygon"


def test_an_undated_publication_carries_the_undated_interval_everywhere():
    coll, items = sink_stac.build_publication(
        {**P, "pub_year": ""}, [], geom=FOOT, bbox=[-112.5, 40.5, -112.0, 41.0], has_cog=True)
    assert coll["ugs:date_unknown"] is True
    assert coll["extent"]["temporal"]["interval"][0][0] == sink_stac.EARLIEST_RECORD
    for it in items:
        assert it["properties"]["datetime"] is None
        assert it["properties"]["start_datetime"] == sink_stac.EARLIEST_RECORD


def test_ids_from_a_series_id_with_a_space_are_path_safe():
    coll, items = sink_stac.build_publication(
        {**P, "series_id": "MP 08-3DM"}, [], geom=FOOT, bbox=[-112.5, 40.5, -112.0, 41.0], has_cog=True)
    assert " " not in coll["_collection_path"] and coll["_collection_path"].count("/") == 2
    for value in [coll["id"], *(it["id"] for it in items)]:
        assert " " not in value and "/" not in value


def test_a_deprecated_edition_keeps_version_links_and_drops_the_mosaic_link():
    coll, items = _build(edition={"version": "1", "deprecated": True,
                                  "successor_href": "https://x/ugs-publications/OFR/OFR-785DM/collection.json"})
    assert coll["deprecated"] is True and stac.VERSION_EXT in coll["stac_extensions"]
    assert any(lk["rel"] == "successor-version" for lk in coll["links"])
    plate = next(it for it in items if it["id"].endswith("_plate"))
    assert not [lk for lk in plate["links"] if lk["rel"] == "related"]


def test_a_layer_missing_from_the_manifest_warns_on_stderr(capsys):
    _build(layers={"geo__DMU": {"rows": 41, "bbox": None, "geometry_types": []}})
    err = capsys.readouterr().err
    assert "no manifest entry for layer 'geo__Faults'" in err


def test_duplicate_item_ids_raise():
    with pytest.raises(ValueError, match="OFR-784DM_geo__Faults"):
        _build(vector_layers=["geo__Faults", "geo__Faults"])


def test_a_layer_named_like_a_reserved_item_is_renamed(capsys):
    _, items = _build(vector_layers=["plate"], has_units=True)
    ids = [it["id"] for it in items]
    assert "OFR-784DM_plate" in ids and "OFR-784DM_layer_plate" in ids
    assert "renamed to 'OFR-784DM_layer_plate'" in capsys.readouterr().err
