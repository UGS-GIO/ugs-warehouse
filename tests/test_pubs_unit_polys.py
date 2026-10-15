"""pubs/unit_polys.py: picking a pub's unit layer from its manifest, and the normalized product."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ugs_warehouse.pubs import unit_polys, vectors

FIXTURES = Path(__file__).parent / "fixtures" / "unit_manifests"


def _manifest(sid: str) -> dict:
    return json.loads((FIXTURES / f"{sid}.json").read_text())


def _codes(warnings: list[dict]) -> set[str]:
    return {w["code"] for w in warnings}


# Real manifests from the rehearsal run: (series id, schema, chosen layer, code column).
CHOSEN = [
    ("OFR-774DM", "gems", "NorthOgden__MapUnitPolys", "MapUnit"),
    ("M-127DR", "template", "AntelopeIsland_GeologicUnits", "UnitSymbol"),
    ("M-180DM", "coverage", None, "UNITSYMBOL"),
    ("M-210", "coverage", "geology_poly", "UNITSYMBOL"),
]


@pytest.mark.parametrize(("sid", "schema", "label", "code"), CHOSEN)
def test_each_schema_is_picked_with_its_code_column(sid, schema, label, code):
    sel, _ = unit_polys.select(_manifest(sid))

    assert sel is not None and sel.schema == schema and sel.code_column == code
    if label:
        assert sel.label.endswith(label)


def test_a_quad_name_in_the_label_does_not_exclude_a_unit_layer():
    sel, _ = unit_polys.select(_manifest("OFR-713DR"))

    assert sel is not None and sel.label.endswith("CedarMesa_BoundaryButte_GeologicUnits")


@pytest.mark.parametrize("sid", ["M-246DM", "M-226DM", "MP-09-4DM", "OFR-776", "SS-152", "RI-287",
                                 "M-215DM"])
def test_landslide_owner_glacial_wetland_and_bedrock_potential_layers_are_never_unit_layers(sid):
    sel, _ = unit_polys.select(_manifest(sid))

    assert sel is None


def test_a_layer_named_like_a_unit_layer_without_its_code_column_is_reported_not_picked():
    manifest = {"layers": [_layer("Quad_GeologicUnits", 5, ["Label", "Name"])]}

    sel, warnings = unit_polys.select(manifest)

    assert sel is None and _codes(warnings) == {unit_polys.UNIT_LAYER_UNUSABLE}


def test_columns_match_ignoring_case_and_underscores():
    sel, _ = unit_polys.select(_manifest("DS-2"))  # Unit_Symbol, Unit_Name, Age_Strat, Description

    assert sel.label.endswith("IBM_UT_GeologicUnits") and sel.schema == "template"
    assert (sel.code_column, sel.name_column, sel.age_column, sel.description_column) == (
        "Unit_Symbol", "Unit_Name", "Age_Strat", "Description")


@pytest.mark.parametrize(("columns", "expected"), [
    (["UnitSymbol", "UnitName", "Age", "Description"], ("Age", "Description")),
    (["UNITSYMBOL", "UNITNAME", "AGE", "NOTES"], ("AGE", "NOTES")),
    (["UnitSymbol", "Unit_Name", "Age_Strat", "Age", "NOTE"], ("Age_Strat", "NOTE")),
    (["UnitSymbol", "Descriptio", "Description"], (None, "Descriptio"))])
def test_age_and_description_take_the_first_candidate_the_layer_has(columns, expected):
    sel, _ = unit_polys.select({"layers": [_layer("Quad_GeologicUnits", 5, columns)]})

    assert (sel.age_column, sel.description_column) == expected


def test_a_shapefile_and_a_geodatabase_copy_of_one_layer_prefer_the_untruncated_columns():
    sel, warnings = unit_polys.select(_manifest("M-249DM"))

    assert sel.label == "ThompsonPoint_M-249DM__ThompsonPoint_GeologicUnits"
    assert unit_polys.UNIT_LAYER_AMBIGUOUS in _codes(warnings)  # A_Units and B_Units are other candidates


def test_gems_joins_the_description_table_on_the_recorded_relationship():
    sel, warnings = unit_polys.select(_manifest("OFR-774DM"))

    assert sel.attr_label == "OFR-774DM_NorthOgden__DescriptionOfMapUnits"
    assert (sel.key, sel.attr_key) == ("MapUnit", "MapUnit")
    assert (sel.name_column, sel.age_column, sel.description_column) == ("Name", "Age", "Description")
    assert unit_polys.UNIT_DMU_UNRELATED not in _codes(warnings)


def test_gems_without_a_recorded_relationship_joins_on_mapunit_and_warns():
    sel, warnings = unit_polys.select(_manifest("OFR-772DM"))

    assert sel.attr_label.endswith("DescriptionOfMapUnits") and sel.key == sel.attr_key == "MapUnit"
    assert unit_polys.UNIT_DMU_UNRELATED in _codes(warnings)


def test_gems_without_a_description_table_has_no_attributes_and_warns():
    manifest = _manifest("OFR-774DM")
    manifest["layers"] = [lay for lay in manifest["layers"] if lay["spatial"]]
    manifest["relationships"] = []

    sel, warnings = unit_polys.select(manifest)

    assert sel.key is None and unit_polys.UNIT_DMU_MISSING in _codes(warnings)


def _layer(label, rows, columns, geometry="MultiPolygon"):
    return {"label": label, "spatial": True, "rows": rows, "geometry_types": [geometry],
            "columns": [{"name": c, "type": "string"} for c in columns]}


def test_ties_prefer_gems_then_template_then_coverage_then_rows_and_warn():
    manifest = {"layers": [
        _layer("a__geounits", 900, ["UNITSYMBOL"]),
        _layer("a__GeologicUnits", 500, ["UnitSymbol"]),
        _layer("a__MapUnitPolys", 10, ["MapUnit"]),
        _layer("b__MapUnitPolys", 20, ["MapUnit"])]}

    sel, warnings = unit_polys.select(manifest)

    assert sel.label == "b__MapUnitPolys"
    assert _codes(warnings) == {unit_polys.UNIT_LAYER_AMBIGUOUS, unit_polys.UNIT_DMU_MISSING}


def test_the_same_layer_under_a_bare_and_a_prefixed_label_is_not_ambiguous():
    manifest = {"layers": [_layer("Quad_GeologicUnits", 5, ["UnitSymbol"]),
                           _layer("x__Quad_GeologicUnits", 5, ["UnitSymbol"])]}

    _, warnings = unit_polys.select(manifest)

    assert warnings == []


def test_a_point_layer_named_like_a_unit_layer_is_skipped():
    manifest = {"layers": [_layer("geounits", 5, ["UNITSYMBOL"], geometry="Point")]}

    assert unit_polys.select(manifest)[0] is None


# --- the product: normalized columns and the DMU join, through the real DuckDB export -------------

gpd = pytest.importorskip("geopandas")
pd = pytest.importorskip("pandas")
shapely_geometry = pytest.importorskip("shapely.geometry")


def _square(x, y, d=100):
    return shapely_geometry.box(x, y, x + d, y + d)


@pytest.fixture
def pub(monkeypatch, tmp_path):
    """GCS double over local files plus a fake tippecanoe; returns (store, uploads)."""
    store: dict[str, bytes] = {}
    uploads: dict[str, bytes] = {}

    def download(path, dest, **kw):
        with open(dest, "wb") as fh:
            fh.write(store[path])

    def upload(local, path, **kw):
        with open(local, "rb") as fh:
            uploads[path] = fh.read()
        return vectors.gcs.FileMeta(len(uploads[path]), "1220" + "ab" * 32)

    def tiler(geojsonl, pmtiles, layer, **kw):
        uploads["geojsonl"] = Path(geojsonl).read_bytes()
        uploads["layer"] = layer.encode()
        Path(pmtiles).write_bytes(b"tiles")

    monkeypatch.setattr(vectors.gcs, "download", download)
    monkeypatch.setattr(unit_polys.gcs, "upload", upload)
    monkeypatch.setattr(unit_polys.sink_pmtiles, "run_tippecanoe", tiler)

    def put(sid, label, frame):
        local = tmp_path / f"{label}.parquet"
        if isinstance(frame, gpd.GeoDataFrame):
            vectors.geoparquet.write(frame, local)
        else:
            frame.to_parquet(local)
        store[f"{vectors.VECTORS_PREFIX}/{sid}/{label}.parquet"] = local.read_bytes()

    return put, uploads


def _gems_manifest():
    return {"warnings": [], "layers": [
        {"label": "g__MapUnitPolys", "spatial": True, "rows": 4, "geometry_types": ["Polygon"],
         "proj_code": "EPSG:26912",
         "columns": [{"name": "MapUnit", "type": "string"}]},
        {"label": "g__DescriptionOfMapUnits", "spatial": False, "rows": 3,
         "columns": [{"name": c, "type": "string"} for c in ("MapUnit", "Name", "FullName", "Age",
                                                             "Description")]}],
        "relationships": [{"origin": "g__MapUnitPolys", "origin_cols": ["MapUnit"],
                           "destination": "g__DescriptionOfMapUnits", "destination_cols": ["MapUnit"]}]}


def _read(path_bytes, tmp_path):
    import duckdb

    local = tmp_path / "out.parquet"
    local.write_bytes(path_bytes)
    return duckdb.sql(f"SELECT series_id, unit_code, unit_name, age, description, source_label "
                      f"FROM read_parquet('{local}') ORDER BY unit_code NULLS LAST, 1").fetchall()


def test_the_product_is_geoparquet_1_1_with_a_bbox_covering_in_4326(pub, tmp_path, monkeypatch):
    import json

    import pyarrow.parquet as pq

    written = []
    real = vectors.geoparquet.write
    monkeypatch.setattr(vectors.geoparquet, "write", lambda frame, path: (written.append(frame), real(frame, path)))
    put, uploads = pub
    polys = gpd.GeoDataFrame({"MapUnit": ["Qal", "Qaf"]},
                             geometry=[_square(424000, 4512000), _square(424200, 4512000)], crs="EPSG:26912")
    put("M-9DM", "g__MapUnitPolys", polys)
    put("M-9DM", "g__DescriptionOfMapUnits", pd.DataFrame({
        "MapUnit": ["Qal", "Qaf"], "Name": ["A", "B"], "FullName": ["A", "B"], "Age": ["Q", "Q"],
        "Description": ["d", "e"]}))

    unit_polys.build("M-9DM", _gems_manifest())

    out = tmp_path / "units.parquet"
    out.write_bytes(uploads[vectors.unit_polys_object("M-9DM")])
    meta = json.loads(pq.read_metadata(out).metadata[b"geo"])
    column = meta["columns"]["geom"]
    assert meta["version"] == "1.1.0" and meta["primary_column"] == "geom"
    assert column["covering"]["bbox"]["xmin"] == ["bbox", "xmin"]
    # No crs member means the spec default, lon/lat OGC:CRS84.
    assert "crs" not in column or column["crs"]["id"]["code"] in (4326, "CRS84")
    table = pq.read_table(out)
    assert set(table.column_names) == {*unit_polys.COLUMNS, "geom", "bbox"}
    assert table.num_rows == 2
    products = [f for f in written if "unit_code" in f.columns]  # `put` writes the inputs the same way
    assert len(products) == 1 and list(products[0].columns) == [*unit_polys.COLUMNS, "geom"]


def test_gems_attributes_are_joined_trimmed_and_blanks_become_null(pub, tmp_path):
    put, uploads = pub
    polys = gpd.GeoDataFrame(
        {"MapUnit": [" Qal ", "Qaf", "Qc", "Qz"]},
        geometry=[_square(424000, 4512000), _square(424200, 4512000), _square(424400, 4512000),
                  _square(424600, 4512000)], crs="EPSG:26912")
    put("M-9DM", "g__MapUnitPolys", polys)
    put("M-9DM", "g__DescriptionOfMapUnits", pd.DataFrame({
        "MapUnit": ["Qal", "Qaf", "Qc"], "Name": [" Alluvium ", "", None],
        "FullName": ["Alluvium full", "Alluvial fan", None], "Age": ["Holocene ", "  ", "Pleistocene"],
        "Description": ["Sand and gravel.  ", None, ""]}))
    manifest = _gems_manifest()

    unit_polys.build("M-9DM", manifest)

    rows = _read(uploads[vectors.unit_polys_object("M-9DM")], tmp_path)
    assert rows == [
        ("M-9DM", "Qaf", "Alluvial fan", None, None, "g__MapUnitPolys"),
        ("M-9DM", "Qal", "Alluvium", "Holocene", "Sand and gravel.", "g__MapUnitPolys"),
        ("M-9DM", "Qc", None, "Pleistocene", None, "g__MapUnitPolys"),
        ("M-9DM", "Qz", None, None, None, "g__MapUnitPolys")]
    assert manifest["unit_layer"] == {"label": "g__MapUnitPolys", "schema": "gems",
                                      "code_column": "MapUnit"}
    assert manifest["unit_polys"]["rows"] == 4 and manifest["unit_polys"]["pmtiles"]["bytes"] == 5
    assert manifest["unit_polys_version"] == unit_polys.UNIT_POLYS_VERSION
    assert uploads["layer"] == b"units"
    feature = json.loads(uploads["geojsonl"].splitlines()[0])
    lon, lat = feature["geometry"]["coordinates"][0][0]
    assert -113 < lon < -111 and 40 < lat < 41  # reprojected from UTM 12N
    assert set(feature["properties"]) == set(unit_polys.COLUMNS)


def test_template_columns_map_to_the_product_columns(pub, tmp_path):
    put, uploads = pub
    layer = gpd.GeoDataFrame(
        {"UnitSymbol": ["Tv", " "], "UnitName": ["Volcanics ", "x"], "Age_Strat": ["Tertiary", None],
         "Descriptio": ["Lava.", "  "]},
        geometry=[_square(424000, 4512000), _square(424200, 4512000)], crs="EPSG:26912")
    put("M-8DM", "Quad_GeologicUnits", layer)
    manifest = {"warnings": [], "layers": [
        {"label": "Quad_GeologicUnits", "spatial": True, "rows": 2, "geometry_types": ["Polygon"],
         "proj_code": "EPSG:26912",
         "columns": [{"name": c, "type": "string"} for c in ("UnitSymbol", "UnitName", "Age_Strat",
                                                             "Descriptio")]}]}

    unit_polys.build("M-8DM", manifest)

    rows = _read(uploads[vectors.unit_polys_object("M-8DM")], tmp_path)
    assert rows == [("M-8DM", "Tv", "Volcanics", "Tertiary", "Lava.", "Quad_GeologicUnits"),
                    ("M-8DM", None, "x", None, None, "Quad_GeologicUnits")]


def test_a_pub_with_no_unit_layer_records_the_version_and_builds_nothing(pub):
    _, uploads = pub
    manifest = {"warnings": [], "layers": [], "unit_layer": {"label": "stale"}}

    unit_polys.build("M-7", manifest)

    assert uploads == {} and manifest == {"warnings": [], "layers": [],
                                          "unit_polys_version": unit_polys.UNIT_POLYS_VERSION}


def test_a_layer_named_unit_polys_is_refused(pub):
    manifest = {"warnings": [], "layers": [_layer("unit_polys", 3, ["x"])]}

    with pytest.raises(ValueError, match="unit_polys"):
        unit_polys.build("M-6", manifest)


def test_the_heal_adds_the_product_to_a_manifest_that_predates_it(monkeypatch):
    old = {"layers": [], "relations_version": 1}
    assert vectors.needs_unit_polys(old)
    assert not vectors.needs_unit_polys({"unit_polys_version": unit_polys.UNIT_POLYS_VERSION})
    assert vectors.needs_of(old).unit_polys and bool(vectors.Needs((), False, False, unit_polys=True))


def test_a_failed_build_leaves_the_version_unset_and_is_an_error(monkeypatch):
    monkeypatch.setattr(vectors.unit_polys, "build",
                        lambda sid, m: (_ for _ in ()).throw(OSError("disk full")))
    store = {f"{vectors.VECTORS_PREFIX}/M-5/_manifest.json": json.dumps(
        {"layers": [], "warnings": [], "unit_polys_version": 0}).encode()}
    monkeypatch.setattr(vectors.gcs, "get_bytes_versioned", lambda p: (store[p], {"version": p}))
    monkeypatch.setattr(vectors.gcs, "put_bytes_if_unchanged",
                        lambda data, p, version, **kw: store.__setitem__(p, data))

    errors = vectors._unit_polys_series("M-5")

    doc = json.loads(store[f"{vectors.VECTORS_PREFIX}/M-5/_manifest.json"])
    assert errors == ["M-5: unit polygons not built: OSError: disk full"]
    assert "unit_polys_version" not in doc
    assert [w["code"] for w in doc["warnings"]] == [unit_polys.UNIT_POLYS_FAILED]


def test_self_heal_builds_the_product_after_the_relationships_and_only_for_the_vectors_job(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(vectors, "_relations_series", lambda sid: calls.append("relations") or [])
    monkeypatch.setattr(vectors, "_geopackage_series", lambda sid: (False, []))
    monkeypatch.setattr(vectors, "_unit_polys_series", lambda sid: calls.append("units") or [])
    needs = {"M-1": vectors.Needs((), False, True, False, False, True)}

    heal = vectors.self_heal(needs)

    assert calls == ["relations", "units"] and heal.unit_built == 1

    calls.clear()
    vectors.self_heal(needs, tiles=False)  # the ingest job leaves the unit product to the vectors job
    assert calls == ["relations"]


def test_a_unit_product_that_failed_to_build_is_a_heal_failure(monkeypatch):
    monkeypatch.setattr(vectors, "_unit_polys_series", lambda sid: [f"{sid}: unit polygons not built: x"])

    heal = vectors.self_heal({"M-1": vectors.Needs((), False, False, False, False, True)})

    assert heal.failed == ["M-1: unit polygons not built: x"] and heal.unit_built == 0
