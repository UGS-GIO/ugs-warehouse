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
    assert ids == {"OFR-784DM_plate", "OFR-784DM__geo__Faults", "OFR-784DM__geo__DMU"}
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
        # A table has no geometry, so no bbox; everything else is placed.
        if it["id"].endswith("geo__DMU"):
            assert it["geometry"] is None and "bbox" not in it
        else:
            assert it["geometry"] and it["bbox"]
    assert coll["sci:doi"] == "10.34191/OFR-784DM" and coll["sci:citation"]
    assert stac.SCI_EXT in coll["stac_extensions"]


def test_each_item_states_its_own_data_crs_and_never_borrows_4326():
    _, items = _build(cog_fields={"proj:code": "EPSG:3857"})
    by_id = {it["id"]: it for it in items}
    assert by_id["OFR-784DM__geo__Faults"]["properties"]["proj:code"] == "EPSG:26912"
    assert "proj:code" not in by_id["OFR-784DM__geo__DMU"]["properties"]      # a table has no CRS
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


def test_layer_uses_its_own_bbox_and_table_has_no_geometry():
    _, items = _build()
    by_id = {it["id"]: it for it in items}
    assert by_id["OFR-784DM__geo__Faults"]["bbox"] == [-112.4, 40.6, -112.1, 40.9]
    assert by_id["OFR-784DM__geo__Faults"]["properties"]["table:row_count"] == 12
    assert by_id["OFR-784DM__geo__DMU"]["properties"]["table:row_count"] == 41
    table = by_id["OFR-784DM__geo__DMU"]
    assert table["geometry"] is None and "bbox" not in table
    assert "ugs:footprint_source" not in table["properties"]
    assert table["assets"]["data"]["table:columns"] == [{"name": "MapUnit", "type": "string"}]
    assert stac.TABLE_EXT in table["stac_extensions"]


def test_items_label_where_their_footprint_came_from():
    _, items = _build()
    src = {it["id"]: it["properties"].get("ugs:footprint_source") for it in items}
    assert src == {"OFR-784DM_plate": "cog", "OFR-784DM__geo__Faults": sink_stac.LAYER_BBOX_SOURCE,
                   "OFR-784DM__geo__DMU": None}


def test_items_without_a_footprint_carry_the_fallback_label():
    _, items = _build(geom=None, bbox=None, fp_source=None)
    src = {it["id"]: it["properties"].get("ugs:footprint_source") for it in items}
    assert src == {"OFR-784DM_plate": sink_stac.FOOTPRINT_FALLBACK,
                   "OFR-784DM__geo__Faults": sink_stac.LAYER_BBOX_SOURCE,
                   "OFR-784DM__geo__DMU": None}


def test_a_collection_on_the_utah_placeholder_says_so():
    coll, _ = sink_stac.build_publication(
        {**P, "series_id": "C-139", "pub_name": "Utah mining 2024"}, [], has_cover=True)
    assert coll["extent"]["spatial"]["bbox"] == [stac.UTAH_BBOX]
    assert coll["ugs:footprint_source"] == sink_stac.FOOTPRINT_FALLBACK
    # Placeholder-placed items and tables add no extent, so it stays the placeholder.
    coll, _ = _build(geom=None, bbox=None, fp_source=None,
                     layers={"geo__Faults": {"rows": 1, "bbox": None}, "geo__DMU": {"rows": 1}})
    assert coll["ugs:footprint_source"] == sink_stac.FOOTPRINT_FALLBACK
    # A real layer bbox is a real extent.
    coll, _ = _build(geom=None, bbox=None, fp_source=None)
    assert coll.get("ugs:footprint_source") != sink_stac.FOOTPRINT_FALLBACK
    assert coll["extent"]["spatial"]["bbox"] == [[-112.4, 40.6, -112.1, 40.9]]
    coll, _ = _build()
    assert coll["ugs:footprint_source"] == "cog"


@pytest.mark.parametrize("tables", [["geo__DMU"], [{"label": "geo__DMU"}]])
def test_is_single_file_takes_a_table_as_a_label_or_a_dict(tables):
    kw = dict(has_cog=True, has_units=False, has_3d=False, vector_layers=[])
    assert sink_stac.is_single_file(**kw, companion_tables=tables) is False
    assert sink_stac.is_single_file(**kw, companion_tables=[]) is True


def test_gems_validation_layers_are_not_published(capsys):
    coll, items = _build(vector_layers=["geo__Faults", "geo__T_1_LineErrors", "t_12_pointerrors"],
                         companion_tables=[{"label": "geo__T_3_PolyErrors"}])
    ids = {it["id"] for it in items}
    assert ids == {"OFR-784DM_plate", "OFR-784DM__geo__Faults"}
    err = capsys.readouterr().err
    for label in ("geo__T_1_LineErrors", "t_12_pointerrors", "geo__T_3_PolyErrors"):
        assert f"skipped '{label}' (GeMS validation output" in err
    assert not sink_stac.is_validation_layer("geo__TopologyErrorsNotes")
    assert not sink_stac.is_validation_layer("T_1_Errorsx")
    assert sink_stac.is_single_file(has_cog=True, has_units=False, has_3d=False,
                                    vector_layers=["geo__T_1_LineErrors"], companion_tables=[])


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
    with pytest.raises(ValueError, match="OFR-784DM__geo__Faults"):
        _build(vector_layers=["geo__Faults", "geo__Faults"])


def test_a_layer_named_like_a_reserved_item_keeps_a_distinct_id(capsys):
    _, items = _build(vector_layers=["plate", "units"], has_units=True)
    ids = {it["id"] for it in items}
    assert {"OFR-784DM_plate", "OFR-784DM_units", "OFR-784DM__plate", "OFR-784DM__units"} <= ids
    assert "renamed" not in capsys.readouterr().err


@pytest.mark.parametrize("sid, pub_id", [
    ("BYU_GS-V24_CO-OPCREEK", "BYU_GS-V24_CO-OPCREEK"),   # single underscores are kept
    ("BYU__GS___V24", "BYU_GS_V24"),                      # runs collapse: no pub id holds `__`
    ("Geologic map of Utah", "Geologic-map-of-Utah"),
])
def test_a_publication_id_never_holds_a_double_underscore(sid, pub_id):
    assert sink_stac.item_id_for(sid) == pub_id
    layer = sink_stac.layer_item_id(sid, "Shapefiles__faults")
    assert layer.split("__", 1) == [pub_id, "Shapefiles__faults"]  # the first `__` ends the pub id
    assert sink_stac.plate_item_id(sid) == f"{pub_id}_plate"


def test_layer_ids_cannot_collide_with_another_publications_id():
    # A pub id can hold `_` (BYU_GS-V24_CO-OPCREEK); `__` separates the label.
    assert sink_stac.layer_item_id("BYU_GS-V24", "CO-OPCREEK") == "BYU_GS-V24__CO-OPCREEK"
    assert sink_stac.layer_item_id("BYU_GS-V24", "CO-OPCREEK") != sink_stac.item_id_for(
        "BYU_GS-V24_CO-OPCREEK")


def _plate_only(**kw):
    return _build(vector_layers=[], companion_tables=[], layers={}, **kw)


def test_a_plate_only_publication_has_no_items_and_the_cog_on_the_collection():
    coll, items = _plate_only(cog_fields={"proj:code": "EPSG:3857"})
    assert items == []
    assert not [lk for lk in coll["links"] if lk["rel"] == "item"]
    cog = coll["assets"]["cog"]
    assert cog["roles"] == ["data", "cloud-optimized"] and cog["proj:code"] == "EPSG:3857"
    assert stac.PROJ_EXT in coll["stac_extensions"]
    assert any(lk["rel"] == "related" and "geologic-maps-24k" in lk["href"] for lk in coll["links"])
    assert coll["extent"]["spatial"]["bbox"] == [[-112.5, 40.5, -112.0, 41.0]]


def test_a_publication_with_plate_and_one_layer_keeps_both_items():
    coll, items = _build(companion_tables=[])
    assert {it["id"] for it in items} == {"OFR-784DM_plate", "OFR-784DM__geo__Faults"}
    assert "cog" not in coll["assets"]


def test_a_lone_table_moves_to_the_collection_with_its_columns():
    coll, items = _build(has_cog=False, vector_layers=[])
    assert items == []
    assert coll["assets"]["data"]["table:columns"] and coll["assets"]["data"]["table:row_count"] == 41
    assert stac.TABLE_EXT in coll["stac_extensions"]


def test_is_single_file_counts_every_kind_of_data_item():
    none = dict(has_cog=False, has_units=False, has_3d=False, vector_layers=[], companion_tables=[])
    assert not sink_stac.is_single_file(**none)
    assert sink_stac.is_single_file(**{**none, "has_cog": True})
    assert sink_stac.is_single_file(**{**none, "vector_layers": ["a"]})
    assert not sink_stac.is_single_file(**{**none, "has_cog": True, "has_units": True})


def test_the_landing_page_is_an_alternate_html_link_not_a_via():
    coll, _ = _build()
    assert not [lk for lk in coll["links"] if lk["rel"] == "via"]
    alt = next(lk for lk in coll["links"] if lk["rel"] == "alternate")
    assert alt["type"] == "text/html" and alt["href"].endswith("?pub=OFR-784DM")


def test_without_a_cover_the_collection_thumbnail_is_the_cog_thumbnail():
    coll, _ = _build(has_cover=False, has_thumb=True)
    assert coll["assets"]["thumbnail"]["roles"] == ["thumbnail"]
    assert coll["assets"]["thumbnail"]["title"] == "Thumbnail"
    coll, _ = _build(has_cover=False, has_thumb=False)
    assert "thumbnail" not in coll["assets"]
    coll, _ = _build(has_cover=True, has_thumb=True)
    assert coll["assets"]["thumbnail"]["title"].startswith("Cover")


def test_a_lone_plate_keeps_its_preview_as_overview_when_the_cover_takes_thumbnail():
    coll, _ = _plate_only(has_cover=True, has_thumb=True)
    assert coll["assets"]["thumbnail"]["title"].startswith("Cover")
    assert coll["assets"]["overview"]["roles"] == ["overview"]
    assert coll["assets"]["overview"]["href"].endswith(".thumb.webp")


def test_a_data_asset_that_collides_with_a_document_asset_is_renamed(capsys):
    att = [{"pub_url": "https://ugspub.nr.utah.gov/publications/ofr/x.xlsx", "extra_data": "data"}]
    coll, _ = sink_stac.build_publication(
        P, att, vector_layers=["geo__Faults"], layers={"geo__Faults": {"rows": 1}})
    assert coll["assets"]["data"]["roles"] == ["data"] and "data_2" in coll["assets"]
    assert coll["assets"]["data_2"]["href"].endswith("geo__Faults.parquet")
    assert "renamed to 'data_2'" in capsys.readouterr().err


def test_a_series_id_with_whitespace_gets_no_doi():
    coll, items = sink_stac.build_publication(
        {**P, "series_id": "557.92 UT1CO"}, [], geom=FOOT, bbox=[-112.5, 40.5, -112.0, 41.0],
        has_cog=True, has_units=True)
    assert len(items) == 2
    for doc in [coll, *(it["properties"] for it in items)]:
        assert "sci:doi" not in doc and "sci:citation" not in doc
    for doc in [coll, *items]:
        assert "cite-as" not in {lk["rel"] for lk in doc["links"]}
        assert stac.SCI_EXT not in doc.get("stac_extensions", [])


def test_the_collection_extent_covers_the_footprint_and_every_item():
    coll, _ = _build(layers={"geo__Faults": {"rows": 12, "bbox": [-113.0, 40.6, -112.1, 41.5],
                                             "geometry_types": ["LineString"]},
                             "geo__DMU": {"rows": 41, "bbox": None, "geometry_types": []}})
    assert coll["extent"]["spatial"]["bbox"] == [[-113.0, 40.5, -112.0, 41.5]]


@pytest.mark.parametrize("label,title,description", [
    ("UtahLandslideMaps__DebrisFlowPaths", "Debris Flow Paths", "From UtahLandslideMaps"),
    ("geounits", "Geounits", None),
    ("SJsymbs50000", "SJsymbs50000", None),
    ("gems__ContactsAndFaults", "Contacts And Faults", "From gems"),
    ("contacts_and_faults", "Contacts And Faults", None),
    ("GIS_DEM_overlay2", "GIS DEM Overlay2", None),
    ("USGSFaults_2020", "USGSFaults 2020", None),
    ("a__", "A", None),
    ("", "", None),
    ("2020_faults", "2020 Faults", None),
    ("a__b__c", "B C", "From a"),
    ("__X", "X", None),
])
def test_layer_title_reads_as_words(label, title, description):
    assert sink_stac.layer_title(label) == (title, description)


def test_layer_and_table_items_carry_readable_titles_and_the_source_dir():
    _, items = sink_stac.build_publication(
        P, [], geom=FOOT, bbox=[-112.5, 40.5, -112.0, 41.0],
        vector_layers=["Maps__DebrisFlowPaths"],
        companion_tables=[{"label": "geounits", "columns": [{"name": "a"}]}],
        layers={"Maps__DebrisFlowPaths": {"rows": 1, "bbox": None}, "geounits": {"rows": 1}})
    by_id = {it["id"]: it for it in items}
    layer = next(i for k, i in by_id.items() if k.endswith("Maps__DebrisFlowPaths"))
    assert layer["properties"]["title"] == "Debris Flow Paths"
    assert layer["properties"]["description"] == "From Maps"
    assert layer["assets"]["data"]["title"] == "Debris Flow Paths"
    table = next(i for k, i in by_id.items() if k.endswith("geounits"))
    assert table["properties"]["title"] == "Geounits" and "description" not in table["properties"]


def test_a_spatial_layer_carries_its_recorded_columns_on_the_data_asset():
    cols = [{"name": "OBJECTID", "type": "int64"}, {"name": "geometry", "type": "geometry"}]
    _, items = _build(layers={"geo__Faults": {"rows": 12, "bbox": None, "columns": cols},
                              "geo__DMU": {"rows": 41}})
    layer = next(i for i in items if i["id"].endswith("geo__Faults"))
    assert layer["assets"]["data"]["table:columns"] == cols
    assert stac.TABLE_EXT in layer["stac_extensions"]
    assert "table:columns" not in layer["properties"]


def test_a_spatial_layer_without_recorded_columns_has_none():
    _, items = _build()
    layer = next(i for i in items if i["id"].endswith("geo__Faults"))
    assert "table:columns" not in layer["assets"]["data"]


def test_layer_titles_name_the_source_dir_when_two_layers_read_alike():
    assert sink_stac.layer_titles(["gems__ContactsAndFaults", "x__ContactsAndFaults",
                                   "contacts_and_faults", "gems__MapUnitPolys"]) == {
        "gems__ContactsAndFaults": ("Contacts And Faults (gems)", "From gems"),
        "x__ContactsAndFaults": ("Contacts And Faults (x)", "From x"),
        "contacts_and_faults": ("Contacts And Faults", None),
        "gems__MapUnitPolys": ("Map Unit Polys", "From gems"),
    }


def test_same_named_layers_in_one_publication_get_distinct_titles():
    labels = ["gems__ContactsAndFaults", "x__ContactsAndFaults"]
    coll, items = sink_stac.build_publication(
        P, [], geom=FOOT, bbox=[-112.5, 40.5, -112.0, 41.0], vector_layers=labels[:1],
        companion_tables=[{"label": labels[1]}],
        layers={lb: {"rows": 1, "bbox": None} for lb in labels})
    titles = {it["id"]: it["properties"]["title"] for it in items}
    assert sorted(titles.values()) == ["Contacts And Faults (gems)", "Contacts And Faults (x)"]
    assert sorted(lk["title"] for lk in coll["links"] if lk["rel"] == "item") == sorted(titles.values())


# ---------------------------------------------------------------- layer PMTiles

TILED = {"geo__Faults": {"rows": 12, "bbox": [-112.4, 40.6, -112.1, 40.9],
                         "geometry_types": ["LineString"], "proj_code": "EPSG:26912",
                         "pmtiles_layer": "geo__Faults"},
         "geo__DMU": {"rows": 41, "bbox": None, "geometry_types": []}}


def _serving_topic_pmtiles(monkeypatch) -> tuple[dict, dict, list[str]]:
    """The pmtiles asset, pmtiles link and extensions a serving-topic item is built with."""
    from ugs_warehouse.vector import sink_stac as vec_sink
    from ugs_warehouse.vector.topics import Topic

    captured: dict = {}
    monkeypatch.setattr(vec_sink, "_bbox", lambda c, v: [0, 1, 2, 3])
    monkeypatch.setattr(vec_sink, "_row_count", lambda c, v: 5)
    monkeypatch.setattr(vec_sink, "_table_columns", lambda c, v: [])
    monkeypatch.setattr(vec_sink.stac, "manual_override", lambda iid: {})
    monkeypatch.setattr(vec_sink.stac, "prior_file_fields", lambda cp, iid: {})
    monkeypatch.setattr(vec_sink.gcs, "exists", lambda p: False)
    monkeypatch.setattr(vec_sink.stac, "build_item", lambda **k: captured.update(k) or {"assets": k["assets"]})
    monkeypatch.setattr(vec_sink.stac, "attach_renders", lambda i: None)
    monkeypatch.setattr(vec_sink.stac, "attach_classification", lambda i: None)
    monkeypatch.setattr(vec_sink.stac, "stamp_file_meta", lambda i: None)
    monkeypatch.setattr(vec_sink.stac, "write_item", lambda i: "stac/path.json")
    vec_sink.write(Topic(schema="hazards", layer="hazards_qfaults_current"), None, "v")
    (link,) = [lk for lk in captured["extra_links"] if lk["rel"] == "pmtiles"]
    return captured["assets"]["pmtiles"], link, captured["stac_extensions"]


def test_a_tiled_layer_item_carries_the_same_pmtiles_shape_as_a_serving_topic(monkeypatch):
    _, items = _build(layers=TILED)
    # After the pub build: this stubs core.stac's builder, which both producers share.
    topic_asset, topic_link, topic_exts = _serving_topic_pmtiles(monkeypatch)
    faults = next(it for it in items if it["id"] == "OFR-784DM__geo__Faults")
    (link,) = [lk for lk in faults["links"] if lk["rel"] == "pmtiles"]
    asset = faults["assets"]["pmtiles"]

    href = "https://maps-assets.geology.utah.gov/geolmap/vectors/OFR-784DM/geo__Faults.pmtiles"
    assert asset["href"] == link["href"] == href
    # The serving topic's asset, plus the tiles' own CRS over the item's native one.
    assert {k: v for k, v in asset.items() if k != "href"} == \
        {**{k: v for k, v in topic_asset.items() if k != "href"}, "proj:code": "EPSG:4326"}
    assert faults["properties"]["proj:code"] == "EPSG:26912"
    assert stac.PROJ_EXT in faults["stac_extensions"]
    assert link["pmtiles:layers"] == ["geo__Faults"]
    assert {k: v for k, v in link.items() if k not in ("href", "pmtiles:layers")} == \
        {k: v for k, v in topic_link.items() if k not in ("href", "pmtiles:layers")}
    assert stac.WEB_MAP_LINKS_EXT in topic_exts and stac.WEB_MAP_LINKS_EXT in faults["stac_extensions"]
    # The GeoParquet stays the data asset; a table is never tiled.
    assert faults["assets"]["data"]["href"].endswith("/geo__Faults.parquet")
    dmu = next(it for it in items if it["id"] == "OFR-784DM__geo__DMU")
    assert "pmtiles" not in dmu["assets"] and not any(lk["rel"] == "pmtiles" for lk in dmu["links"])


def test_a_layer_without_pmtiles_carries_no_tiles():
    _, items = _build()
    faults = next(it for it in items if it["id"] == "OFR-784DM__geo__Faults")
    assert "pmtiles" not in faults["assets"]
    assert not any(lk["rel"] == "pmtiles" for lk in faults["links"])
    assert stac.WEB_MAP_LINKS_EXT not in faults["stac_extensions"]


def test_a_tiled_layers_index_entry_carries_its_web_map_link():
    coll, items = _build(layers=TILED)
    faults = next(it for it in items if it["id"] == "OFR-784DM__geo__Faults")
    entry = stac._pub_item_index_entry(faults, coll, prefix="ugs-publications/OFR/OFR-784DM/")
    (link,) = [lk for lk in entry["links"] if lk["rel"] == "pmtiles"]
    assert link == {"rel": "pmtiles", "type": "application/vnd.pmtiles", "pmtiles:layers": ["geo__Faults"],
                    "href": "https://maps-assets.geology.utah.gov/geolmap/vectors/OFR-784DM/geo__Faults.pmtiles"}
    assert entry["assets"]["pmtiles"]["roles"] == ["visual"]
    assert stac._is_mappable(faults)


def test_a_lone_tiled_layer_moves_its_tiles_and_link_to_the_collection():
    coll, items = _build(has_cog=False, companion_tables=[], layers=TILED)
    assert items == []
    assert coll["assets"]["data"]["proj:code"] == "EPSG:26912"
    # The tiles keep their own EPSG:4326; the layer's CRS and row count stay off them.
    assert coll["assets"]["pmtiles"]["proj:code"] == "EPSG:4326"
    assert not any(k.startswith("table:") for k in coll["assets"]["pmtiles"])
    (link,) = [lk for lk in coll["links"] if lk["rel"] == "pmtiles"]
    assert link["href"] == coll["assets"]["pmtiles"]["href"]
    assert stac.WEB_MAP_LINKS_EXT in coll["stac_extensions"]
    entry = stac._collection_index_entry(coll, prefix="ugs-publications/OFR/OFR-784DM/")
    assert any(lk["rel"] == "pmtiles" for lk in entry["links"])


# --- relationships and grouping ------------------------------------------------------------------

VEC = stac.config.public_url("geolmap/vectors/OFR-784DM")
ITEMS = stac.config.public_url(f"{stac.config.STAC_PREFIX}/ugs-publications/OFR/OFR-784DM")
DMU_COLS = [{"name": "MapUnit", "type": "string"}, {"name": "Age", "type": "string"}]
MUP_TO_DMU = {"origin": "geo__MapUnitPolys", "origin_cols": ["MapUnit"],
              "destination": "geo__DMU", "destination_cols": ["MapUnit"],
              "cardinality": "many_to_one", "detected_by": "inferred", "confidence": 100, "overlap": 100.0}


def _related(**kw):
    args = dict(vector_layers=["geo__MapUnitPolys", "geo__Faults"],
                companion_tables=[{"label": "geo__DMU", "columns": DMU_COLS},
                                  {"label": "geo__Glossary", "columns": None}],
                layers={"geo__MapUnitPolys": {"rows": 3, "bbox": [-112.4, 40.6, -112.1, 40.9],
                                              "kind": "layer", "source": "OFR784DM_MillsJunction"},
                        "geo__Faults": {"rows": 2, "bbox": [-112.4, 40.6, -112.1, 40.9]},
                        "geo__DMU": {"rows": 41, "kind": "table", "source": "OFR784DM_MillsJunction"},
                        "geo__Glossary": {"rows": 9}},
                relationships=[MUP_TO_DMU])
    coll, items = _build(**{**args, **kw})
    return coll, {it["id"].removeprefix("OFR-784DM_").removeprefix("_"): it for it in items}


def test_a_layer_carries_its_related_table_in_the_serving_topic_shape():
    _, items = _related()
    mup = items["geo__MapUnitPolys"]

    assert mup["assets"]["geo__DMU"] == {
        "href": f"{VEC}/geo__DMU.parquet", "type": "application/vnd.apache.parquet",
        "roles": ["data", "related"], "title": "DMU",
        # Flipped, as vector/related.py `_inverted_rel` does: the table's column -> the layer's.
        "ugs:foreign_keys": [{"fields": ["MapUnit"],
                              "reference": {"resource": "OFR-784DM__geo__MapUnitPolys",
                                            "href": f"{VEC}/geo__MapUnitPolys.parquet",
                                            "fields": ["MapUnit"]},
                              "ugs:detected_by": "inferred", "ugs:confidence": 100}],
        "table:columns": DMU_COLS}
    # The layer's own outgoing key sits on its data asset, as on a serving topic.
    assert mup["assets"]["data"]["ugs:foreign_keys"] == [{
        "fields": ["MapUnit"],
        "reference": {"resource": "OFR-784DM__geo__DMU", "href": f"{VEC}/geo__DMU.parquet",
                      "fields": ["MapUnit"]},
        "ugs:detected_by": "inferred", "ugs:confidence": 100}]
    assert stac.TABLE_EXT in mup["stac_extensions"]
    # The table links back to the layer that references it.
    back = [lk for lk in items["geo__DMU"]["links"] if lk["rel"] == "related"]
    assert back == [{"rel": "related", "href": f"{ITEMS}/OFR-784DM__geo__MapUnitPolys/OFR-784DM__geo__MapUnitPolys.json",
                     "type": "application/geo+json", "title": "Map Unit Polys"}]
    assert not [lk for lk in mup["links"] if lk["rel"] == "related"]


def test_the_related_table_matches_what_the_viewer_click_join_reads():
    """viewer/src/stac.ts relatedJoins: a related asset's single-column FK gives
    childField = fields[0] (the table's column) and parentField = reference.fields[0] (the clicked
    feature's)."""
    _, items = _related(relationships=[{**MUP_TO_DMU, "origin_cols": ["UnitCode"]}])
    (fk,) = items["geo__MapUnitPolys"]["assets"]["geo__DMU"]["ugs:foreign_keys"]
    assert (fk["fields"][0], fk["reference"]["fields"][0]) == ("MapUnit", "UnitCode")


def test_a_spatial_destination_is_a_related_link_both_ways():
    rel = {**MUP_TO_DMU, "origin": "geo__Faults", "destination": "geo__MapUnitPolys",
           "detected_by": "declared", "confidence": None}
    _, items = _related(relationships=[rel])

    def related(item_id):
        return [lk["href"].rsplit("/", 1)[-1] for lk in items[item_id]["links"] if lk["rel"] == "related"]

    assert related("geo__Faults") == ["OFR-784DM__geo__MapUnitPolys.json"]
    assert related("geo__MapUnitPolys") == ["OFR-784DM__geo__Faults.json"]
    assert not any("related" in a["roles"] for a in items["geo__Faults"]["assets"].values())
    (fk,) = items["geo__Faults"]["assets"]["data"]["ugs:foreign_keys"]
    assert fk["ugs:detected_by"] == "declared" and "ugs:confidence" not in fk


def test_two_keys_to_one_table_share_its_asset():
    second = {**MUP_TO_DMU, "origin_cols": ["Label"], "destination_cols": ["Label"]}
    _, items = _related(relationships=[MUP_TO_DMU, second])
    fks = items["geo__MapUnitPolys"]["assets"]["geo__DMU"]["ugs:foreign_keys"]
    assert [fk["fields"] for fk in fks] == [["MapUnit"], ["Label"]]
    assert len([lk for lk in items["geo__DMU"]["links"] if lk["rel"] == "related"]) == 1


def test_a_relationship_to_a_validation_or_empty_layer_is_skipped_silently(capsys):
    to_errors = {**MUP_TO_DMU, "destination": "geo__T_1_PolyErrors"}
    to_empty = {**MUP_TO_DMU, "destination": "geo__Stations"}
    _, items = _related(relationships=[to_errors, to_empty], empty_layers=["geo__Stations"],
                        companion_tables=[{"label": "geo__DMU"}, {"label": "geo__T_1_PolyErrors"}])
    assert set(items["geo__MapUnitPolys"]["assets"]) == {"data"}
    assert "ugs:foreign_keys" not in items["geo__MapUnitPolys"]["assets"]["data"]
    assert "relationship" not in capsys.readouterr().err


@pytest.mark.parametrize("rel", [
    {**MUP_TO_DMU, "destination": "geo__Nowhere"},             # names a label not published
    {**MUP_TO_DMU, "destination": "geo__MapUnitPolys"},        # relates a layer to itself
])
def test_a_relationship_naming_any_other_unpublished_layer_fails_the_pub(rel):
    with pytest.raises(ValueError, match="not a separate published layer"):
        _related(relationships=[rel])


def test_a_foreign_key_carries_detected_by_as_recorded():
    declared = {**MUP_TO_DMU, "detected_by": "declared"}
    del declared["confidence"]
    _, items = _related(relationships=[declared])
    (fk,) = items["geo__MapUnitPolys"]["assets"]["geo__DMU"]["ugs:foreign_keys"]
    assert fk["ugs:detected_by"] == "declared" and "ugs:confidence" not in fk


def test_a_related_table_named_like_another_asset_gets_its_own_key():
    rel = {**MUP_TO_DMU, "destination": "pmtiles"}
    _, items = _related(relationships=[rel],
                        companion_tables=[{"label": "pmtiles", "columns": None}])
    assets = items["geo__MapUnitPolys"]["assets"]
    assert "related" in assets["pmtiles_2"]["roles"] and "pmtiles" not in assets


def test_items_and_their_collection_links_carry_kind_and_source_dataset():
    coll, items = _related()
    props = {k: (it["properties"].get("ugs:layer_kind"), it["properties"].get("ugs:source_dataset"))
             for k, it in items.items()}
    assert items["geo__DMU"]["properties"]["ugs:source_key"] == "OFR784DM_MillsJunction"
    assert items["geo__Faults"]["properties"]["ugs:source_key"] == "geo"
    assert props == {
        "plate": (None, None),
        "geo__MapUnitPolys": ("layer", "OFR784DM Mills Junction"),
        "geo__Faults": ("layer", "Geo"),               # from the label when the manifest has none
        "geo__DMU": ("table", "OFR784DM Mills Junction"),
        "geo__Glossary": ("supporting", "Geo")}
    links = {lk["href"]: lk for lk in coll["links"] if lk["rel"] == "item"}
    dmu = links["./OFR-784DM__geo__DMU/OFR-784DM__geo__DMU.json"]
    assert dmu["ugs:layer_kind"] == "table" and dmu["ugs:source_dataset"] == "OFR784DM Mills Junction"
    assert dmu["ugs:source_key"] == "OFR784DM_MillsJunction"  # grouping key; the title is for display
    assert "ugs:layer_kind" not in links["./OFR-784DM_plate/OFR-784DM_plate.json"]


def test_ids_of_renamed_layers_are_what_relationships_point_at(capsys):
    # `faults` and `Faults` are distinct labels whose ids would merge on a case-insensitive disk.
    rel = {**MUP_TO_DMU, "origin": "faults"}
    _, items = _related(vector_layers=["Faults", "faults"], relationships=[rel])
    (fk,) = items["faults_2"]["assets"]["geo__DMU"]["ugs:foreign_keys"]
    assert fk["reference"]["resource"] == "OFR-784DM__faults_2"
    (back,) = [lk for lk in items["geo__DMU"]["links"] if lk["rel"] == "related"]
    assert back["href"].endswith("/OFR-784DM__faults_2/OFR-784DM__faults_2.json")


@pytest.mark.parametrize("source, title", [
    ("Cedar_Mesa_S__Shapefiles", "Cedar Mesa S"),                 # OFR-443DM's quad folders
    ("The_Rincon__Shapefiles", "The Rincon"),
    ("GIS_Data__Mexican_Hat__shp", "Mexican Hat"),
    ("Bluff__Geology.gdb", "Bluff · Geology"),                     # two distinctive folders
    ("OFR784DM_MillsJunction", "OFR784DM Mills Junction"),         # a GeMS geodatabase stem
    ("Shapefiles", "Shapefiles"),                                  # nothing distinctive: kept
    ("GIS__Shapefiles", "Shapefiles"),
])
def test_a_source_title_reads_its_whole_folder_path(source, title):
    assert sink_stac.source_title(source) == title


def test_quad_folders_of_one_publication_get_distinct_source_titles():
    labels = ["Cedar_Mesa_S__Shapefiles__faults", "The_Rincon__Shapefiles__unitsymspoly"]
    _, items = _build(has_cog=False, vector_layers=labels, companion_tables=[], layers={})
    got = {(it["properties"]["ugs:source_key"], it["properties"]["ugs:source_dataset"])
           for it in items}
    assert got == {("Cedar_Mesa_S__Shapefiles", "Cedar Mesa S"),
                   ("The_Rincon__Shapefiles", "The Rincon")}


def test_kind_comes_from_the_relationships_published_not_the_manifest(capsys):
    # The table's only relationship is to a validation layer, which is never published.
    rel = {**MUP_TO_DMU, "origin": "geo__T_1_PointErrors"}
    _, items = _related(vector_layers=["geo__MapUnitPolys", "geo__T_1_PointErrors"], relationships=[rel])
    assert items["geo__DMU"]["properties"]["ugs:layer_kind"] == "supporting"  # manifest said "table"
    coll, _ = _related()
    assert {lk.get("ugs:layer_kind") for lk in coll["links"]
            if lk["rel"] == "item" and lk["href"].endswith("geo__DMU.json")} == {"table"}


def _ofr784dm_publication():
    from test_pubs_relations import ofr784dm_layers

    from ugs_warehouse.pubs import relations

    layers = ofr784dm_layers()
    return _build(has_cog=False, relationships=relations.infer(layers),
                  vector_layers=[lay.label for lay in layers if lay.spatial],
                  companion_tables=[{"label": lay.label, "columns": None}
                                    for lay in layers if not lay.spatial],
                  layers={})


def test_a_table_most_map_layers_point_at_is_a_reference(capsys):
    _, items = _ofr784dm_publication()
    roles = {(it["id"].removeprefix("OFR-784DM__"), key): a["roles"]
             for it in items for key, a in it["assets"].items() if "related" in a["roles"]}
    # Seven of the eleven published map layers cite DataSources; three use the DMU.
    assert {k: r for k, r in roles.items() if k[1] == "DataSources"} == {
        (lay, "DataSources"): ["data", "related", "reference"]
        for lay in ("MapUnitPolys", "GeologicLines", "CartographicLines", "CartographicPoints",
                    "ContactsAndFaults", "GeologicPoints", "MapUnitOverlays", "Glossary")}
    assert {r == ["data", "related"] for k, r in roles.items() if k[1] != "DataSources"} == {True}
    assert {k[1] for k in roles} == {"DataSources", "DescriptionOfMapUnits", "GeoMaterialDict"}
    kinds = {it["id"].removeprefix("OFR-784DM__"): it["properties"].get("ugs:layer_kind") for it in items}
    assert (kinds["DataSources"], kinds["Glossary"], kinds["MiscellaneousMapInformation"]) == (
        "table", "table", "supporting")


def test_a_layer_extracted_from_an_attachment_names_it_on_its_item_and_collection_link():
    file = {"key": "gis_data_zip", "title": "GIS Data - Zip", "path": "shp/faults.shp"}
    layers = {"geo__Faults": {"rows": 12, "bbox": [-112.4, 40.6, -112.1, 40.9],
                              "geometry_types": ["LineString"], "source_file": file},
              "geo__DMU": {"rows": 41, "bbox": None, "geometry_types": []}}
    coll, items = _build(layers=layers)

    by_id = {it["id"]: it for it in items}
    assert by_id["OFR-784DM__geo__Faults"]["properties"]["ugs:source_file"] == "GIS Data - Zip"
    assert "ugs:source_file" not in by_id["OFR-784DM__geo__DMU"]["properties"]
    links = {lk["href"]: lk for lk in coll["links"] if lk["rel"] == "item"}
    faults = links["./OFR-784DM__geo__Faults/OFR-784DM__geo__Faults.json"]
    assert faults["ugs:source_file"] == "GIS Data - Zip"
    # The manifest's key is the attachment's asset on the Collection, from the same slug function.
    assert file["key"] in coll["assets"]


def test_the_attachment_key_is_the_one_the_collection_gives_its_asset():
    from ugs_warehouse.pubs import identity

    att = [{"pub_url": "https://ugspub.nr.utah.gov/publications/ofr/a.xlsx",
            "extra_data": "Appendix A - Excel"}]
    coll, _ = sink_stac.build_publication(
        P, att, geom=FOOT, bbox=[-112.5, 40.5, -112.0, 41.0], fp_source="cog", has_cog=False,
        has_cover=False, vector_layers=[], companion_tables=[], layers={})

    assert identity.attachment_key("Appendix A - Excel") == "appendix_a_excel"
    assert "appendix_a_excel" in coll["assets"]
