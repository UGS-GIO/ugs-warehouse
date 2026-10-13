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


def _ofr784dm() -> list[relations.Layer]:
    doc = json.loads(FIXTURE.read_text())
    layers = []
    for lay in doc["layers"]:
        df = pd.DataFrame({c: [None if i < 0 else v["values"][i] for i in v["rows"]]
                           for c, v in lay["columns"].items()})
        layers.append(relations.Layer(lay["name"], lay["name"], doc["gdb"], relations.profile_frame(df)))
    return layers


def _layer(label: str, group: str = "g", **columns) -> relations.Layer:
    return relations.Layer(label, label, group, relations.profile_frame(pd.DataFrame(columns)))


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
    rels = relations.infer(_ofr784dm())

    assert _pairs(rels) == {
        "MapUnitPolys.MapUnit -> DescriptionOfMapUnits.MapUnit",
        "GeologicPoints.MapUnit -> DescriptionOfMapUnits.MapUnit",
        "OrientationPoints.MapUnit -> DescriptionOfMapUnits.MapUnit",
        "DescriptionOfMapUnits.GeoMaterial -> GeoMaterialDict.GeoMaterial",
        *(f"{lay}.DataSourceID -> DataSources.DataSources_ID" for lay in (
            "MapUnitPolys", "GeologicLines", "CartographicLines", "CartographicPoints",
            "ContactsAndFaults", "GeologicPoints", "MapUnitOverlays")),
    }
    mup = next(r for r in rels if r["origin"] == "MapUnitPolys" and r["destination"] == "DescriptionOfMapUnits")
    assert mup == {"origin": "MapUnitPolys", "origin_cols": ["MapUnit"],
                   "destination": "DescriptionOfMapUnits", "destination_cols": ["MapUnit"],
                   "cardinality": "many_to_one", "detected_by": "inferred",
                   "confidence": 100, "overlap": 100.0}


def test_of_equally_scored_keys_the_one_named_for_its_table_wins():
    # MapUnitPolys.Symbol -> DescriptionOfMapUnits.Symbol also scores 100; MapUnit is in the table's name.
    rels = relations.infer(_ofr784dm())
    mup = [r for r in rels if r["origin"] == "MapUnitPolys" and r["destination"] == "DescriptionOfMapUnits"]
    assert [r["origin_cols"] for r in mup] == [["MapUnit"]]


def test_a_column_unique_only_because_it_holds_one_value_is_not_a_key():
    # OFR-784DM's one-row CartographicPoints has DataSourceID "This study", the constant every other
    # layer carries; a one-row error table has IsException 1, like its sibling's.
    rels = relations.infer([
        _layer("Lines", DataSourceID=["This study"] * 3),
        _layer("Points", DataSourceID=["This study"]),
        _layer("Polys", DataSourceID=["This study", "This study"]),
    ])
    assert rels == []


def test_null_keys_are_allowed_but_duplicates_are_not():
    dmu = _layer("DMU", MapUnit=["Qal", None, "Qaf"])  # a heading row has no MapUnit
    polys = _layer("Polys", MapUnit=["Qal", "Qaf", "Qal"])
    assert _pairs(relations.infer([polys, dmu])) == {"Polys.MapUnit -> DMU.MapUnit"}
    dup = _layer("DMU", MapUnit=["Qal", "Qal", "Qaf"])
    assert relations.infer([polys, dup]) == []


def test_overlap_below_90_percent_is_rejected():
    dmu = _layer("DMU", MapUnit=[f"U{i}" for i in range(10)])
    nine_of_ten = _layer("A", MapUnit=[f"U{i}" for i in range(9)] * 2 + ["X"])
    eight_of_ten = _layer("B", MapUnit=[f"U{i}" for i in range(8)] * 2 + ["X", "Y"])
    rels = relations.infer([dmu, nine_of_ten, eight_of_ten])
    assert _pairs(rels) == {"A.MapUnit -> DMU.MapUnit"}
    assert rels[0]["overlap"] == 90.0 and rels[0]["confidence"] == 94


def test_names_must_pass_the_gate_however_well_values_overlap():
    glossary = _layer("Glossary", Term=["certain", "questionable"])
    lines = _layer("Lines", IdentityConfidence=["certain", "questionable", "certain"])
    assert relations.infer([glossary, lines]) == []


def test_a_layer_never_relates_to_itself_or_across_source_datasets():
    same = _layer("A", Code=["a", "b"], CodeID=["a", "b"])
    assert relations.infer([same]) == []
    dmu = _layer("DMU", group="one.gdb", MapUnit=["Qal", "Qaf"])
    polys = _layer("Polys", group="two.gdb", MapUnit=["Qal", "Qaf"])
    assert relations.infer([dmu, polys]) == []


def test_integer_and_string_values_compare_as_text():
    sources = relations.Layer("Sources", "Sources", "g", relations.profile_frame(
        pd.DataFrame({"Sources_ID": pd.array([1, 2, 3], dtype="Int64")})))
    rels = relations.infer([sources, _layer("Lines", SourceID=["1", "2", "2"])])
    assert _pairs(rels) == {"Lines.SourceID -> Sources.Sources_ID"}


def test_a_one_to_one_pair_found_both_ways_is_kept_once_toward_the_larger_key():
    a = _layer("A", Code=["x", "y"])
    b = _layer("B", Code=["x", "y", "z"])
    rels = relations.infer([a, b])
    assert _pairs(rels) == {"A.Code -> B.Code"} and rels[0]["cardinality"] == "one_to_one"


def test_geometry_objectid_fid_and_shape_columns_are_never_profiled():
    gpd = pytest.importorskip("geopandas")
    Point = pytest.importorskip("shapely.geometry").Point
    gdf = gpd.GeoDataFrame({"OBJECTID": [1, 2], "FID": [1, 2], "Shape_Length": [1, 2],
                            "MapUnit": ["a", "b"], "Area": [1.5, 2.5],
                            "geometry": [Point(0, 0), Point(1, 1)]}, crs="EPSG:4326")
    assert set(relations.profile_frame(gdf)) == {"MapUnit"}
    table = pa.table({"OBJECTID": [1, 2], "SHAPE_Area": [1, 2], "Code": ["a", "b"]})
    assert set(relations.profile_table(table)) == {"Code"}


def test_samples_and_keys_are_capped(monkeypatch):
    monkeypatch.setattr(relations, "SAMPLE_SIZE", 3)
    monkeypatch.setattr(relations, "KEY_CAP", 4)
    col = relations.profile_column(pa.array([f"v{i}" for i in range(5)]))
    assert len(col.sample) == 3 and col.unique and col.keys is None  # over the cap: never a target
    assert relations.profile_column(pa.array(["a", "b", "c"])).keys is not None
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
    labels = {"DescriptionOfMapUnits": "a", "MapUnitPolys": "b"}
    many = {**DECLARED, "cardinality": "ManyToMany", "mapping_table_name": "map"}
    assert relations.orient_declared(many, labels)[0] is None
    rel, why = relations.orient_declared(DECLARED, {"MapUnitPolys": "b"})
    assert rel is None and "not extracted" in why
    rel, why = relations.orient_declared({**DECLARED, "right_table_fields": ["A", "B"]}, labels)
    assert rel is None and "unmatched" in why


def test_merge_keeps_declared_and_drops_inferred_joins_of_the_same_tables():
    declared = [{"origin": "A", "destination": "B", "detected_by": "declared"}]
    inferred = [{"origin": "B", "destination": "A", "detected_by": "inferred"},
                {"origin": "C", "destination": "B", "detected_by": "inferred"}]
    assert relations.merge(declared, inferred) == [declared[0], inferred[1]]


def test_layer_kind_and_source_from_a_label():
    rels = [{"origin": "geo__Polys", "destination": "geo__DMU"}]
    assert relations.layer_kind(True, "geo__Faults", rels) == "layer"
    assert relations.layer_kind(False, "geo__DMU", rels) == "table"
    assert relations.layer_kind(False, "geo__Glossary", rels) == "supporting"
    assert relations.source_of_label("OFR784DM_MillsJunction__MapUnitPolys", "OFR-784DM") \
        == "OFR784DM_MillsJunction"
    assert relations.source_of_label("faults", "M-246DM") == "M-246DM"
    assert relations.layer_name("OFR784DM_MillsJunction__MapUnitPolys") == "MapUnitPolys"
