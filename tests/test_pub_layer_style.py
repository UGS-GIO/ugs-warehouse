"""pubs/layer_style.py: a publication layer's MapLibre style from the map's own colours."""
from __future__ import annotations

import pyarrow as pa
import pytest

from ugs_warehouse.pubs import layer_style as L

UNITS = "geo__MapUnitPolys"
DMU = "geo__DescriptionOfMapUnits"
JOIN = {"origin": UNITS, "origin_cols": ["MapUnit"], "destination": DMU, "destination_cols": ["MapUnit"],
        "detected_by": "inferred", "confidence": 100}


def _entries(colour_type="string"):
    return {
        UNITS: {"label": UNITS, "spatial": True, "geometry_types": ["MultiPolygon"],
                "columns": [{"name": "MapUnit", "type": "string"}]},
        DMU: {"label": DMU, "spatial": False,
              "columns": [{"name": "MapUnit", "type": "string"}, {"name": "AreaFillRGB", "type": colour_type}]},
    }


def _reader(tables):
    return lambda label, cols: pa.table({c: tables[label][c] for c in cols})


def _fill(styled):
    return styled.doc["layers"][0]["paint"]["fill-color"]


def test_parse_color_reads_gems_rgb_hex_and_rejects_the_rest():
    assert L.parse_color("255;0;128") == "#ff0080"
    assert L.parse_color(" 255 ; 0 ;128 ") == "#ff0080"
    assert L.parse_color("10, 20, 30") == "#0a141e"
    assert L.parse_color("#AABBCC") == "#aabbcc"
    for bad in (None, "", "   ", "256;0;0", "1;2", "a;b;c", "red, yellow, light-gray", "1;2;3;4"):
        assert L.parse_color(bad) is None


def test_dmu_colours_become_a_match_on_the_unit_code_with_a_fallback():
    read = _reader({
        DMU: {"MapUnit": ["Qal", "Tv", None, "Qc"], "AreaFillRGB": ["255;255;0", "0; 128 ;255", "9;9;9", None]},
        UNITS: {"MapUnit": ["Qal", "Qal", "Tv", "Qc", "Zz"]},
    })

    styled = L.build(_entries()[UNITS], _entries(), [JOIN], read)

    assert styled.kind == "units"
    assert _fill(styled) == ["match", ["to-string", ["get", "MapUnit"]],
                             "Qal", "#ffff00", "Tv", "#0080ff", L.FALLBACK_FILL]
    # Qc has a blank colour and Zz is not in the DMU: both draw in the fallback fill.
    assert styled.fallback_rows == 2
    assert styled.warnings == []
    assert [lay["type"] for lay in styled.doc["layers"]] == ["fill", "line"]
    assert styled.doc["layers"][1]["paint"]["line-width"] < 1


def test_a_heading_row_with_no_map_unit_is_skipped():
    read = _reader({DMU: {"MapUnit": [None, "", "Qal"], "AreaFillRGB": ["1;2;3", "4;5;6", "7;8;9"]},
                    UNITS: {"MapUnit": ["Qal"]}})
    styled = L.build(_entries()[UNITS], _entries(), [JOIN], read)
    assert _fill(styled) == ["match", ["to-string", ["get", "MapUnit"]], "Qal", "#070809", L.FALLBACK_FILL]


def test_a_malformed_or_conflicting_colour_is_reported_and_the_rest_still_match():
    read = _reader({DMU: {"MapUnit": ["Qal", "Tv", "Tv"], "AreaFillRGB": ["bad", "1;2;3", "9;9;9"]},
                    UNITS: {"MapUnit": ["Qal", "Tv"]}})
    styled = L.build(_entries()[UNITS], _entries(), [JOIN], read)
    assert _fill(styled)[2:4] == ["Tv", "#010203"]
    assert any("not read as R;G;B" in w and "Qal" in w for w in styled.warnings)
    assert any("two colours" in w and "Tv" in w for w in styled.warnings)


def test_the_join_is_found_from_either_end():
    flipped = {"origin": DMU, "origin_cols": ["MapUnit"], "destination": UNITS,
               "destination_cols": ["MapUnit"], "detected_by": "declared"}
    read = _reader({DMU: {"MapUnit": ["Qal"], "AreaFillRGB": ["1;2;3"]}, UNITS: {"MapUnit": ["Qal"]}})
    assert L.build(_entries()[UNITS], _entries(), [flipped], read).kind == "units"


def test_an_empty_dmu_colour_column_gets_the_default_without_a_warning():
    # AreaFillRGB is optional; a pub that leaves it empty has it recorded as type `null`.
    entries = _entries("null")
    read = _reader({DMU: {"MapUnit": ["Qal"], "AreaFillRGB": [None]}, UNITS: {"MapUnit": ["Qal"]}})
    styled = L.build(entries[UNITS], entries, [JOIN], read)
    assert styled.kind == "default" and styled.warnings == []


def test_a_populated_dmu_column_that_reads_as_no_colour_warns():
    read = _reader({DMU: {"MapUnit": ["Qal"], "AreaFillRGB": ["tan"]}, UNITS: {"MapUnit": ["Qal"]}})
    styled = L.build(_entries()[UNITS], _entries(), [JOIN], read)
    assert styled.kind == "default"
    assert any("no usable colours" in w for w in styled.warnings)


def test_a_layer_with_no_dmu_gets_the_viewers_default_look():
    entry = {"label": "faults", "spatial": True, "geometry_types": ["LineString"], "columns": []}
    styled = L.build(entry, {"faults": entry}, [], _reader({}))
    assert styled.kind == "default"
    kinds = [lay["type"] for lay in styled.doc["layers"]]
    assert kinds == ["fill", "line", "circle"]  # defaultVectorLayers: one layer per geometry type
    assert styled.doc["layers"][0]["paint"] == {
        "fill-color": L.NEUTRAL, "fill-opacity": 0.15, "fill-outline-color": L.NEUTRAL}
    assert len({lay["id"] for lay in styled.doc["layers"]}) == 3


def test_a_polygon_layer_with_its_own_colour_column_uses_it():
    entry = {"label": "units", "spatial": True, "geometry_types": ["Polygon"],
             "columns": [{"name": "RGB", "type": "string"}]}
    read = _reader({"units": {"RGB": ["10;20;30", "10;20;30", "#ABCDEF", None, "wat"]}})
    styled = L.build(entry, {"units": entry}, [], read)
    assert styled.kind == "column"
    assert _fill(styled) == ["match", ["to-string", ["get", "RGB"]],
                             "#ABCDEF", "#abcdef", "10;20;30", "#0a141e", L.FALLBACK_FILL]
    assert any("wat" in w for w in styled.warnings)


def test_a_colour_column_of_prose_is_not_a_palette():
    entry = {"label": "units", "spatial": True, "geometry_types": ["Polygon"],
             "columns": [{"name": "Color", "type": "string"}]}
    read = _reader({"units": {"Color": ["light-gray", "red, yellow"]}})
    styled = L.build(entry, {"units": entry}, [], read)
    assert styled.kind == "default" and styled.warnings == []


def test_a_line_layer_never_reads_a_colour_column():
    entry = {"label": "lines", "spatial": True, "geometry_types": ["LineString"],
             "columns": [{"name": "RGB", "type": "string"}]}
    styled = L.build(entry, {"lines": entry}, [], lambda *a: pytest.fail("read a column"))
    assert styled.kind == "default"
