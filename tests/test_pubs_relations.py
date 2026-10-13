"""pubs/relations.py: relationships between a publication's layers and tables, declared by the
geodatabase or inferred from column names and values with ugs-ingest's FK scoring."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from ugs_warehouse.pubs import relations

pd = pytest.importorskip("pandas")
pa = pytest.importorskip("pyarrow")

# The string and integer columns of OFR-784DM's geodatabase (Mills Junction), minus long free text
# and annotation classes, dictionary-encoded: each column's distinct values and a per-row index.
FIXTURE = Path(__file__).parent / "fixtures" / "ofr784dm_keys.json"


def ofr784dm_layers() -> list[relations.Layer]:
    doc = json.loads(FIXTURE.read_text())
    layers = []
    for lay in doc["layers"]:
        df = pd.DataFrame({c: [None if i < 0 else v["values"][i] for i in v["rows"]]
                           for c, v in lay["columns"].items()})
        columns, failed = relations.profile_frame(df, keys=not lay["spatial"])
        assert failed == []
        layers.append(relations.Layer(lay["name"], lay["name"], doc["gdb"], columns, lay["spatial"]))
    return layers


def _layer(label: str, group: str = "g", spatial: bool = False, **columns) -> relations.Layer:
    profiled, failed = relations.profile_frame(pd.DataFrame(columns), keys=not spatial)
    assert failed == []
    return relations.Layer(label, label, group, profiled, spatial)


def _pairs(rels: list[dict]) -> set[str]:
    return {f"{r['origin']}.{r['origin_cols'][0]} -> {r['destination']}.{r['destination_cols'][0]}"
            for r in rels}


# --- the name rule (ugs-ingest scoreNameMatch) --------------------------------------------------

@pytest.mark.parametrize("src, pk, table, score", [
    ("MapUnit", "MapUnit", "DescriptionOfMapUnits", 40),          # exact column name
    ("DataSourceID", "DataSources_ID", "DataSources", 30),         # stem inside the key
    ("DataSourcesID", "DataSource_ID", "DataSource", 30),          # ...singular/plural folded
    ("CategoriesID", "Category_ID", "Category", 30),
    ("UnitID", "Code", "geo_unit", 28),                            # table ends with _stem
    ("StationID", "Code", "FieldStations", 22),                    # stem inside the table name
    ("LocationSourceID", "DataSources_ID", "DataSources", 10),    # only a generic _id suffix
    ("Notes", "MapUnit", "DescriptionOfMapUnits", 0),
])
def test_name_score_ports_the_ugs_ingest_rule(src, pk, table, score):
    assert relations.name_score(src, pk, table) == score


# --- inference on a real GeMS geodatabase -------------------------------------------------------

def test_ofr784dm_relationships_are_the_gems_joins_and_nothing_else():
    rels = relations.infer(ofr784dm_layers())

    assert _pairs(rels) == {
        "MapUnitPolys.MapUnit -> DescriptionOfMapUnits.MapUnit",
        "GeologicPoints.MapUnit -> DescriptionOfMapUnits.MapUnit",
        "OrientationPoints.MapUnit -> DescriptionOfMapUnits.MapUnit",
        "DescriptionOfMapUnits.GeoMaterial -> GeoMaterialDict.GeoMaterial",
        *(f"{lay}.DataSourceID -> DataSources.DataSources_ID" for lay in (
            "MapUnitPolys", "GeologicLines", "CartographicLines", "CartographicPoints",
            "ContactsAndFaults", "GeologicPoints", "MapUnitOverlays")),
        # The low-name bar: 8 citations, every one a DataSources_ID.
        "Glossary.DefinitionSourceID -> DataSources.DataSources_ID",
    }
    mup = next(r for r in rels if r["origin"] == "MapUnitPolys" and r["destination"] == "DescriptionOfMapUnits")
    assert mup == {"origin": "MapUnitPolys", "origin_cols": ["MapUnit"],
                   "destination": "DescriptionOfMapUnits", "destination_cols": ["MapUnit"],
                   "cardinality": "many_to_one", "detected_by": "inferred",
                   "confidence": 100, "overlap": 100.0}
    glossary = next(r for r in rels if r["origin"] == "Glossary")
    assert (glossary["confidence"], glossary["overlap"]) == (70, 100.0)


def test_ofr784dm_inferred_joins_target_only_non_spatial_tables():
    layers = ofr784dm_layers()
    spatial = {lay.label for lay in layers if lay.spatial}
    assert {r["destination"] for r in relations.infer(layers)} & spatial == set()
    # A spatial layer keeps no key sets at all.
    assert all(c.keys is None for lay in layers if lay.spatial for c in lay.columns.values())


def test_of_equally_scored_keys_the_one_named_for_its_table_wins():
    # MapUnitPolys.Symbol -> DescriptionOfMapUnits.Symbol also scores 100; MapUnit is in the table's name.
    rels = relations.infer(ofr784dm_layers())
    mup = [r for r in rels if r["origin"] == "MapUnitPolys" and r["destination"] == "DescriptionOfMapUnits"]
    assert [r["origin_cols"] for r in mup] == [["MapUnit"]]


# --- the reviewer's false joins: each must fail ---------------------------------------------------

PROBES = {
    "generic Id, small numbered layer": [("faults", dict(Id=[0] * 50)), ("dikes", dict(Id=[0, 1, 2, 3, 4])),
                                         ("contacts", dict(Id=[0] * 200))],
    "Y/N flags, 2-row layer": [("ContactsAndFaults", dict(IsConcealed=["N", "Y"] * 100)),
                               ("SmallLines", dict(IsConcealed=["N", "Y"]))],
    "integer codes 1..3, 3-row layer": [("LandslideDeposits", dict(Confidence=[1, 2, 3, 1, 2, 3, 2])),
                                        ("LandslideScarps", dict(Confidence=[1, 2, 3]))],
    "OBJECTID_1 row numbers": [("a", dict(OBJECTID_1=list(range(1, 40)))),
                               ("b", dict(OBJECTID_1=list(range(1, 10))))],
    "Type string, small layer": [("Folds", dict(Type=["anticline", "syncline"] * 30)),
                                 ("FoldAxes", dict(Type=["anticline", "syncline", "monocline"]))],
}


@pytest.mark.parametrize("probe", sorted(PROBES))
def test_the_reviewers_false_joins_between_map_layers_fail(probe):
    # Each probe's layers are shapefiles or feature classes: spatial, so never an inferred target.
    layers = [_layer(label, spatial=True, **cols) for label, cols in PROBES[probe]]
    assert relations.infer(layers) == []


@pytest.mark.parametrize("probe", sorted(set(PROBES) - {"Type string, small layer"}))
def test_the_reviewers_false_joins_fail_even_between_tables(probe):
    # Without the spatial rule the key rules still stop them: `Id` and `OBJECTID_n` are never keys,
    # a key needs 3 distinct values, and 1..N row numbering is no key unless named for its table.
    # (Folds.Type into a 3-row non-spatial table of distinct Types reads as a real lookup.)
    layers = [_layer(label, **cols) for label, cols in PROBES[probe]]
    assert relations.infer(layers) == []


def test_a_key_needs_three_distinct_values():
    two = _layer("Points", DataSourceID=["This study", "Olig, 1994"])
    three = _layer("Sources", DataSourceID=["This study", "Olig, 1994", "Clark, 2020"])
    lines = _layer("Lines", DataSourceID=["This study"] * 3 + ["Olig, 1994"])
    assert relations.infer([lines, two]) == []
    assert _pairs(relations.infer([lines, three])) == {"Lines.DataSourceID -> Sources.DataSourceID"}


def test_a_spatial_layer_is_never_an_inferred_target():
    dmu = _layer("DMU", spatial=True, MapUnit=["Qal", "Qaf", "Qc"])
    polys = _layer("Polys", spatial=True, MapUnit=["Qal", "Qaf", "Qal"])
    assert relations.infer([polys, dmu]) == []


def test_row_numbering_is_a_key_only_when_named_for_its_table():
    lookup = _layer("Lookup", Code=[1, 2, 3, 4, 5])
    codes = _layer("Lookup", Code=[10, 20, 30, 40, 50])
    sources = _layer("Sources", Sources_ID=[0, 1, 2, 3])
    polys = _layer("Polys", Code=[1, 2, 2, 5], SourceID=[0, 1, 1, 3])
    assert relations.infer([polys, lookup]) == []
    assert _pairs(relations.infer([_layer("Polys", Code=[10, 20, 20]), codes])) == {
        "Polys.Code -> Lookup.Code"}
    assert _pairs(relations.infer([polys, sources])) == {"Polys.SourceID -> Sources.Sources_ID"}


def test_generic_ids_and_row_numbers_are_never_profiled():
    gpd = pytest.importorskip("geopandas")
    Point = pytest.importorskip("shapely.geometry").Point
    gdf = gpd.GeoDataFrame({"OBJECTID": [1, 2], "OBJECTID_1": [1, 2], "FID": [1, 2],
                            "FID_faults": [1, 2], "Id": [0, 0], "Shape_Length": [1, 2],
                            "MapUnit": ["a", "b"], "Area": [1.5, 2.5],
                            "geometry": [Point(0, 0), Point(1, 1)]}, crs="EPSG:4326")
    assert set(relations.profile_frame(gdf)[0]) == {"MapUnit"}
    table = pa.table({"OBJECTID": [1, 2], "SHAPE_Area": [1, 2], "ID": [1, 2], "Code": ["a", "b"]})
    assert set(relations.profile_table(table)[0]) == {"Code"}


def test_integer_columns_with_nulls_arrive_as_floats_and_are_not_candidates():
    df = pd.DataFrame({"Sources_ID": [1, None, 3]})  # how GDAL hands a nullable integer field over
    assert relations.profile_frame(df) == ({}, [])


def test_a_column_that_cannot_be_profiled_is_reported_and_the_rest_are_kept(monkeypatch):
    real = relations.profile_column

    def flaky(arr, keys=True):
        if "boom" in arr.to_pylist():
            raise TypeError("unhashable")
        return real(arr, keys)

    monkeypatch.setattr(relations, "profile_column", flaky)
    columns, failed = relations.profile_frame(pd.DataFrame({"Bad": ["boom"], "MapUnit": ["Qal"]}))
    assert set(columns) == {"MapUnit"} and failed == ["Bad: TypeError: unhashable"]


# --- other inference rules ------------------------------------------------------------------------

def test_null_keys_are_allowed_but_duplicates_are_not():
    dmu = _layer("DMU", MapUnit=["Qal", None, "Qaf", "Qc"])  # a heading row has no MapUnit
    polys = _layer("Polys", MapUnit=["Qal", "Qaf", "Qal"])
    assert _pairs(relations.infer([polys, dmu])) == {"Polys.MapUnit -> DMU.MapUnit"}
    dup = _layer("DMU", MapUnit=["Qal", "Qal", "Qaf", "Qc"])
    assert relations.infer([polys, dup]) == []


def test_overlap_below_90_percent_is_rejected():
    dmu = _layer("DMU", MapUnit=[f"U{i}" for i in range(10)])
    nine_of_ten = _layer("A", MapUnit=[f"U{i}" for i in range(9)] * 2 + ["X"])
    eight_of_ten = _layer("B", MapUnit=[f"U{i}" for i in range(8)] * 2 + ["X", "Y"])
    rels = relations.infer([dmu, nine_of_ten, eight_of_ten])
    assert _pairs(rels) == {"A.MapUnit -> DMU.MapUnit"}
    assert rels[0]["overlap"] == 90.0 and rels[0]["confidence"] == 94


def test_names_must_pass_the_gate_however_well_values_overlap():
    glossary = _layer("Glossary", Term=["certain", "questionable", "approximate"])
    lines = _layer("Lines", IdentityConfidence=["certain", "questionable", "certain"])
    assert relations.infer([glossary, lines]) == []


def test_the_low_name_bar_needs_every_value_three_shared_and_an_id_key():
    sources = _layer("DataSources", DataSources_ID=["DAS1", "DAS2", "DAS3", "DAS4"],
                     Source=["a", "b", "c", "d"])
    three = _layer("Points", LocationSourceID=["DAS1", "DAS2", "DAS3", "DAS1"])
    two = _layer("Lines", LocationSourceID=["DAS1", "DAS2", "DAS2"])
    stray = _layer("Polys", LocationSourceID=["DAS1", "DAS2", "DAS3", "X", "X"])
    rels = relations.infer([sources, three, two, stray])
    assert _pairs(rels) == {"Points.LocationSourceID -> DataSources.DataSources_ID"}
    assert rels[0]["confidence"] == 70
    # Not an `*ID` key: the generic suffix alone never reaches it.
    assert relations.infer([_layer("DataSources", Code=["DAS1", "DAS2", "DAS3"]), three]) == []


def test_the_low_name_bar_needs_a_word_shared_with_the_target():
    # Two unrelated tables whose codes happen to coincide: no word in common, no join.
    zones = _layer("Zones", ZoneCode_ID=["A1", "B2", "C3", "D4"])
    types = _layer("Features", TypeID=["A1", "B2", "C3", "A1"])
    assert relations.infer([zones, types]) == []
    # The same values under a name that shares "zone" with the table do join.
    assert _pairs(relations.infer([zones, _layer("Features", ZoneTypeID=["A1", "B2", "C3", "A1"])])) \
        == {"Features.ZoneTypeID -> Zones.ZoneCode_ID"}


@pytest.mark.parametrize("name, tokens", [
    ("DefinitionSourceID", {"definition", "source"}),
    ("DataSources", {"data", "source"}),           # plurals folded
    ("DataSources_ID", {"data", "source"}),
    ("ZoneCode_ID", {"zone", "code"}),
    ("TypeID", {"type"}),
    ("Map_Key", set()),                            # words under 4 characters do not count
    ("GeoMaterialDict", {"material", "dict"}),
])
def test_name_tokens(name, tokens):
    assert relations._tokens(name) == tokens


def test_a_layer_never_relates_to_itself_or_across_source_datasets():
    same = _layer("A", Code=["a", "b", "c"], CodeID=["a", "b", "c"])
    assert relations.infer([same]) == []
    dmu = _layer("DMU", group="one", MapUnit=["Qal", "Qaf", "Qc"])
    polys = _layer("Polys", group="two", MapUnit=["Qal", "Qaf"])
    assert relations.infer([dmu, polys]) == []


def test_integer_and_string_values_compare_as_text():
    sources = relations.Layer("Sources", "Sources", "g", relations.profile_frame(
        pd.DataFrame({"Sources_ID": pd.array([1, 2, 3], dtype="Int64")}))[0], False)
    rels = relations.infer([sources, _layer("Lines", SourceID=["1", "2", "2"])])
    assert _pairs(rels) == {"Lines.SourceID -> Sources.Sources_ID"}


def test_a_one_to_one_pair_found_both_ways_is_kept_once_toward_the_larger_key():
    a = _layer("A", Code=["x", "y", "z"])
    b = _layer("B", Code=["x", "y", "z", "w"])
    rels = relations.infer([a, b])
    assert _pairs(rels) == {"A.Code -> B.Code"} and rels[0]["cardinality"] == "one_to_one"


def test_samples_and_keys_are_capped(monkeypatch):
    monkeypatch.setattr(relations, "SAMPLE_SIZE", 3)
    monkeypatch.setattr(relations, "KEY_CAP", 4)
    col = relations.profile_column(pa.array([f"v{i}" for i in range(5)]))
    assert len(col.sample) == 3 and col.unique and col.keys is None  # over the cap: never a target
    assert relations.profile_column(pa.array(["a", "b", "c"])).keys is not None
    assert relations.profile_column(pa.array(["a", "b", "c"]), keys=False).keys is None
    assert relations.profile_column(pa.array([None, None], type=pa.string())) is None
    assert relations.profile_column(pa.array([1.5, 2.5])) is None


# --- declared relationship classes --------------------------------------------------------------

def _ogrinfo(monkeypatch, stdout: str, returncode: int = 0, stderr: str = ""):
    monkeypatch.setattr(relations.shutil, "which", lambda name: "/usr/bin/ogrinfo")
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)

    monkeypatch.setattr(relations.subprocess, "run", run)
    return calls


# What `ogrinfo -json -so` 3.12 prints for a geodatabase relationship class (checked against a
# geodatabase written with GDAL's relationship API).
DECLARED = {"type": "Association", "related_table_type": "features", "cardinality": "OneToMany",
            "left_table_name": "DescriptionOfMapUnits", "right_table_name": "MapUnitPolys",
            "left_table_fields": ["MapUnit"], "right_table_fields": ["MapUnit"],
            "forward_path_label": "", "backward_path_label": ""}


def test_read_declared_returns_ogrinfos_relationship_classes(monkeypatch):
    calls = _ogrinfo(monkeypatch, json.dumps({"layers": [], "relationships": {"dmu_rel": DECLARED}}))
    rels, why = relations.read_declared("/w/geo.gdb")
    assert why is None and rels == [{"name": "dmu_rel", **DECLARED}]
    assert calls == [["/usr/bin/ogrinfo", "-json", "-so", "/w/geo.gdb"]]


def test_read_declared_says_why_when_it_cannot_read_them(monkeypatch):
    monkeypatch.setattr(relations.shutil, "which", lambda name: None)
    assert relations.read_declared("g.gdb") == (None, "ogrinfo is not on PATH")
    _ogrinfo(monkeypatch, json.dumps({"layers": []}))
    rels, why = relations.read_declared("g.gdb")
    assert rels is None and "no `relationships`" in why
    _ogrinfo(monkeypatch, "", returncode=1, stderr="unable to open")
    rels, why = relations.read_declared("g.gdb")
    assert rels is None and "exited 1: unable to open" in why


def test_a_declared_one_to_many_runs_from_the_related_table_to_the_base_table():
    labels = {"DescriptionOfMapUnits": "geo__DescriptionOfMapUnits", "MapUnitPolys": "geo__MapUnitPolys"}
    rel, why = relations.orient_declared(DECLARED, labels)
    assert why is None and rel == {
        "origin": "geo__MapUnitPolys", "origin_cols": ["MapUnit"],
        "destination": "geo__DescriptionOfMapUnits", "destination_cols": ["MapUnit"],
        "cardinality": "many_to_one", "detected_by": "declared"}
    assert relations.orient_declared(DECLARED, {k.lower(): v for k, v in labels.items()})[0] == rel


def test_declared_relationships_that_cannot_be_a_join_are_skipped_with_a_reason():
    labels = {"DescriptionOfMapUnits": "a", "MapUnitPolys": "b", "MapUnitPolys__ATTACH": "c"}
    many = {**DECLARED, "cardinality": "ManyToMany", "mapping_table_name": "map"}
    assert relations.orient_declared(many, labels)[0] is None
    rel, why = relations.orient_declared(DECLARED, {"MapUnitPolys": "b"})
    assert rel is None and "not extracted" in why
    rel, why = relations.orient_declared({**DECLARED, "right_table_fields": ["A", "B"]}, labels)
    assert rel is None and "unmatched" in why
    media = {**DECLARED, "related_table_type": "media"}
    attach = {**DECLARED, "left_table_name": "MapUnitPolys", "right_table_name": "MapUnitPolys__ATTACH",
              "left_table_fields": ["GlobalID"], "right_table_fields": ["REL_GLOBALID"]}
    for rel in (media, attach):
        out, why = relations.orient_declared(rel, labels)
        assert out is None and "attachment" in why


def test_merge_keeps_declared_and_drops_inferred_joins_of_the_same_tables():
    declared = [{"origin": "A", "destination": "B", "detected_by": "declared"}]
    inferred = [{"origin": "B", "destination": "A", "detected_by": "inferred"},
                {"origin": "C", "destination": "B", "detected_by": "inferred"}]
    assert relations.merge(declared, inferred) == [declared[0], inferred[1]]


# --- kinds and scopes -----------------------------------------------------------------------------

def test_layer_kind_and_source_from_a_label():
    rels = [{"origin": "geo__Polys", "destination": "geo__DMU"}]
    assert relations.layer_kind(True, "geo__Faults", rels) == "layer"
    assert relations.layer_kind(False, "geo__DMU", rels) == "table"
    assert relations.layer_kind(False, "geo__Glossary", rels) == "supporting"
    assert relations.source_of_label("OFR784DM_MillsJunction__MapUnitPolys", "OFR-784DM") \
        == "OFR784DM_MillsJunction"
    assert relations.source_of_label("sub__Geo__Faults", "M-1") == "sub__Geo"  # collided gdb
    assert relations.source_of_label("faults", "M-246DM") == "M-246DM"
    assert relations.layer_name("OFR784DM_MillsJunction__MapUnitPolys") == "MapUnitPolys"


def test_collided_spots_a_folder_prefix_given_because_a_name_was_taken():
    assert relations.collided({"faults", "sub__faults"})
    assert relations.collided({"Geo__Faults", "sub__Geo__Faults"})
    assert not relations.collided({"Geo__Faults", "Geo__DMU", "faults", "dikes"})
    assert not relations.collided({"faults", "faults_2"})
