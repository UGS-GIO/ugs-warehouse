"""pubs/sheets.py: spreadsheet and CSV attachments become typed tables, and point layers when the
sheet has coordinates inside Utah."""
from __future__ import annotations

import datetime

import pytest

from ugs_warehouse.pubs import sheets

pd = pytest.importorskip("pandas")
openpyxl = pytest.importorskip("openpyxl")
pytest.importorskip("geopandas")


def _xlsx(path, *sheet_rows, names=None):
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for i, rows in enumerate(sheet_rows):
        ws = wb.create_sheet((names or [f"Sheet{i + 1}"] * len(sheet_rows))[i])
        for row in rows:
            ws.append(row)
    wb.save(path)
    return str(path)


HEADER = ["Sample", "Latitude", "Longitude", "Depth", "Date"]


def test_a_sheet_with_a_title_above_its_header_keeps_cell_types(tmp_path):
    f = _xlsx(tmp_path / "t.xlsx", [
        [], ["Table 1. Analyses of the quadrangle", None, None, None, None], [],
        HEADER,
        ["A-1", 40.1, -111.2, 12, datetime.datetime(2020, 5, 17)],
        ["A-2", 40.2, -111.3, 15.5, datetime.datetime(2021, 6, 1)]])

    sheet = sheets.read(f, "Sheet1")

    assert list(sheet.frame.columns) == HEADER
    assert sheet.frame["Depth"].tolist() == [12, 15.5]
    assert str(sheet.frame["Depth"].dtype).startswith(("int", "float"))
    assert str(sheet.frame["Date"].dtype).startswith("datetime64")
    assert sheet.warnings == []


def test_a_sheet_with_no_header_row_is_skipped_with_a_warning(tmp_path):
    f = _xlsx(tmp_path / "t.xlsx", [[1, 2, 3], [4, 5, 6], [7, 8, 9]])

    sheet = sheets.read(f, "Sheet1")

    assert sheet.frame is None
    assert "no header row" in sheet.warnings[-1]


def test_an_empty_sheet_and_a_header_only_sheet_are_skipped_with_a_warning(tmp_path):
    f = _xlsx(tmp_path / "t.xlsx", [[None, None]], [["A", "B"]], names=["Empty", "HeaderOnly"])

    empty, header_only = sheets.read(f, "Empty"), sheets.read(f, "HeaderOnly")

    assert empty.frame is None and "empty" in empty.warnings[-1]
    assert header_only.frame is None and "no data rows" in header_only.warnings[-1]


def test_units_and_label_rows_under_the_header_are_folded_into_it(tmp_path):
    f = _xlsx(tmp_path / "t.xlsx", [
        [None, "SiO2", "Al2O3", "CaO", "Ba", "Sr"], [None, "%", "%", "%", "ppm", "ppm"],
        ["Sample ID", None, None, None, None, None],
        ["A-1", 74.1, 11.5, 0.9, 380, 90], ["A-2", 61.4, 15.7, 3.5, 410, 120]])

    sheet = sheets.read(f, "Sheet1")

    assert list(sheet.frame.columns) == [
        "Sample ID", "SiO2 (%)", "Al2O3 (%)", "CaO (%)", "Ba (ppm)", "Sr (ppm)"]
    assert sheet.frame["Ba (ppm)"].tolist() == [380, 410]
    assert sheet.frame["SiO2 (%)"].tolist() == [74.1, 61.4]
    assert any("read as one header" in w for w in sheet.warnings)


def test_a_column_that_mixes_numbers_and_text_is_kept_as_text_and_named(tmp_path):
    f = _xlsx(tmp_path / "t.xlsx", [["Sample", "Ag"], ["A-1", "<0.5"], ["A-2", 3]])

    sheet = sheets.read(f, "Sheet1")

    assert sheet.frame["Ag"].tolist() == ["<0.5", "3"]
    assert "Ag" in sheet.warnings[-1]


def test_a_csv_is_typed_and_keeps_identifiers_with_leading_zeros(tmp_path):
    p = tmp_path / "t.csv"
    p.write_text("Sample,Depth,Code\nA-1,12,007\nA-2,15.5,010\n")

    sheet = sheets.read(str(p), None)

    assert sheet.frame["Depth"].tolist() == [12, 15.5]
    assert sheet.frame["Code"].tolist() == ["007", "010"]


def test_a_cp1252_csv_is_read_with_a_warning(tmp_path):
    p = tmp_path / "t.csv"
    p.write_bytes("Name,Value\nCaf\xe9,1\n".encode("cp1252"))

    sheet = sheets.read(str(p), None)

    assert sheet.frame["Name"].tolist() == ["Caf\xe9"]
    assert "cp1252" in sheet.warnings[0]


def test_duplicate_column_names_are_renamed_ignoring_case():
    names, renames = sheets.dedupe_columns(["Name", "NAME", "name", "Age", "name_2"])

    assert names == ["Name", "NAME_3", "name_4", "Age", "name_2"]
    assert renames == [("NAME", "NAME_3"), ("name", "name_4")]


def test_a_sheet_with_duplicate_headers_is_renamed_with_a_warning(tmp_path):
    f = _xlsx(tmp_path / "t.xlsx", [["Name", "NAME", "Age"], ["a", "b", 1]])

    sheet = sheets.read(f, "Sheet1")

    assert list(sheet.frame.columns) == ["Name", "NAME_2", "Age"]
    assert "NAME -> NAME_2" in sheet.warnings[0]


def test_latitude_longitude_columns_inside_utah_give_points_and_count_the_rest():
    frame = pd.DataFrame({
        "Sample": ["in", "blank", "far", "swapped", "in2"],
        "Lat": [40.1, None, 12.0, -111.0, 38.5],
        "Long": [-111.2, -111.0, -111.0, 40.0, -112.5]})
    warnings: list[str] = []

    gdf = sheets.points(frame, warnings)

    assert gdf.crs.to_epsg() == 4326
    assert gdf["Sample"].tolist() == ["in", "in2"]
    assert [(round(p.x, 1), round(p.y, 1)) for p in gdf.geometry] == [(-111.2, 40.1), (-112.5, 38.5)]
    assert "3 of 5 rows" in warnings[0] and "1 with blank" in warnings[0] and "2 outside" in warnings[0]


def test_a_coordinate_pair_outside_utah_is_not_moved_into_it():
    warnings: list[str] = []

    assert sheets.points(pd.DataFrame({"lat": [34.0], "lon": [-118.0]}), warnings) is None
    assert "no row falls inside Utah" in warnings[0]


def test_a_sheet_without_coordinate_columns_has_no_points():
    warnings: list[str] = []

    assert sheets.points(pd.DataFrame({"Sample": ["a"], "Depth": [1.0]}), warnings) is None
    assert warnings == []


def test_utm_columns_are_used_only_when_the_sheet_names_a_zone_and_datum():
    named = pd.DataFrame({"Easting_NAD83_Z12": [432000.0], "Northing_NAD83_Z12": [4430000.0]})
    bare = pd.DataFrame({"Easting": [432000.0], "Northing": [4430000.0]})
    warnings: list[str] = []

    gdf = sheets.points(named, warnings)
    assert gdf is not None and warnings == []
    assert round(gdf.geometry.iloc[0].y, 1) == 40.0 and -113 < gdf.geometry.iloc[0].x < -111

    assert sheets.points(bare, warnings) is None
    assert "does not give the zone and datum" in warnings[0] and "table only" in warnings[0]


# ---- real header names, the open-once reader, the xls reader, CSV through Arrow, size cap ----

@pytest.mark.parametrize("lat, lon", [
    ("Latitude WGS846", "Longitude WGS846"),
    ("Latitude (°N)", "Longitude (°W)"),
    ("Surf Lat", "Surf Lon"),
    ("LAT_DD", "LONG_DD"),
    ("y_dd", "x_dd")])
def test_real_latitude_and_longitude_header_names_are_recognised(lat, lon):
    frame = pd.DataFrame({"Sample": ["a"], lat: [40.1], lon: [-111.2]})

    gdf = sheets.points(frame, [])

    assert gdf is not None and round(gdf.geometry.iloc[0].x, 1) == -111.2


@pytest.mark.parametrize("east, north", [("UTM easting", "UTM northing"), ("Easting1(m)", "Northing1(m)")])
def test_real_easting_northing_names_are_recognised_and_still_need_a_zone_and_datum(east, north):
    bare = pd.DataFrame({east: [432000.0], north: [4430000.0]})
    warnings: list[str] = []

    assert sheets.points(bare, warnings) is None
    assert east in warnings[0] and "does not give the zone and datum" in warnings[0]

    named = bare.assign(Zone=12, Datum="NAD83")
    gdf = sheets.points(named, [])
    assert gdf is not None and round(gdf.geometry.iloc[0].y, 1) == 40.0


def test_a_coordinate_looking_column_that_is_not_used_is_named_in_a_warning():
    warnings: list[str] = []

    assert sheets.points(pd.DataFrame({"Sample": ["a"], "Latitude (°N)": [40.1]}), warnings) is None
    assert "Latitude" in warnings[0] and "no matching partner" in warnings[0]

    warnings = []
    assert sheets.points(pd.DataFrame({"Lat": [10.0], "Lon": [-111.0]}), warnings) is None
    assert "Lat/Lon" in warnings[0] and "inside Utah" in warnings[0]


def test_a_combined_lat_long_column_is_not_taken_for_either_axis():
    assert sheets.points(pd.DataFrame({"Lat/Long": ["40.1,-111.2"], "Sample": ["a"]}), []) is None


def test_a_row_of_text_under_the_header_is_data_unless_it_is_units():
    f_rows = [["County", "Quad", "Note"], ["Juab", "Juab", "Juab"], ["Millard", "Delta", "y"]]
    sheet = sheets.read(_xlsx_rows(f_rows), "Sheet1")

    assert sheet.frame["County"].tolist() == ["Juab", "Millard"]
    assert not any("one header" in w for w in sheet.warnings)


def _xlsx_rows(rows):
    import tempfile

    path = tempfile.mkdtemp() + "/t.xlsx"
    return _xlsx(path, rows)


def test_an_xls_workbook_is_read_through_xlrd_with_dates_and_numbers_typed():
    sheet = sheets.read("tests/fixtures/gravity.xls", "Gravity")

    assert list(sheet.frame.columns) == ["Station", "Latitude", "Longitude", "Obs Gravity", "Date"]
    assert sheet.frame["Obs Gravity"].tolist() == [979500.25, 979501.5]
    assert str(sheet.frame["Date"].dtype).startswith("datetime64")
    assert sheets.sheet_names("tests/fixtures/gravity.xls") == ["Gravity"]
    assert sheets.points(sheet.frame, []) is not None


def test_a_workbook_is_opened_once_for_all_of_its_sheets(tmp_path, monkeypatch):
    f = _xlsx(tmp_path / "t.xlsx", [["A", "B"], [1, 2]], [["C", "D"], [3, 4]], names=["One", "Two"])
    opened = []
    real = openpyxl.load_workbook
    monkeypatch.setattr(openpyxl, "load_workbook", lambda *a, **k: opened.append(k) or real(*a, **k))

    with sheets.Workbook(f) as book:
        first, second = (sheets.read(f, n, book) for n in book.names)

    assert len(opened) == 1 and opened[0]["read_only"] is True
    assert first.frame["A"].tolist() == [1] and second.frame["C"].tolist() == [3]


def test_a_file_over_the_size_cap_is_an_error_naming_it(tmp_path, monkeypatch):
    f = _xlsx(tmp_path / "big.xlsx", [["A"], [1]])
    c = tmp_path / "big.csv"
    c.write_text("A\n1\n")
    monkeypatch.setattr(sheets, "MAX_SHEET_BYTES", 3)

    for path, sheet in ((f, "Sheet1"), (str(c), None)):
        with pytest.raises(ValueError, match=r"big\.(xlsx|csv) is .* limit"):
            sheets.read(path, sheet)
        with pytest.raises(ValueError, match="limit"):
            sheets.sheet_names(path)


def test_a_csv_with_a_ragged_row_leaves_it_out_with_a_warning(tmp_path):
    p = tmp_path / "t.csv"
    p.write_text("A,B\n1,2\n3,4,5\n6,7\n")

    sheet = sheets.read(str(p), None)

    assert sheet.frame["A"].tolist() == [1, 6]
    assert "1 rows with a different number of fields" in sheet.warnings[0]


def test_a_csv_with_a_multi_line_quoted_title_above_the_header_is_read_aligned(tmp_path):
    p = tmp_path / "t.csv"
    p.write_text('"Table 1.\nSamples"\n"Notes\nmore",,\nSample,Depth,Code\nA-1,12,x\nA-2,15,y\n')

    sheet = sheets.read(str(p), None)

    assert list(sheet.frame.columns) == ["Sample", "Depth", "Code"]
    assert sheet.frame["Sample"].tolist() == ["A-1", "A-2"]
    assert sheet.frame["Depth"].tolist() == [12, 15]


def test_a_csv_header_cell_that_spans_two_lines_is_read_aligned(tmp_path):
    p = tmp_path / "t.csv"
    p.write_text('"Sample\nID",Depth\nA-1,12\nA-2,15\n')

    sheet = sheets.read(str(p), None)

    assert list(sheet.frame.columns) == ["Sample ID", "Depth"]
    assert sheet.frame["Sample ID"].tolist() == ["A-1", "A-2"]


def test_a_csv_whose_parsers_disagree_fails_naming_the_file():
    import pyarrow as pa

    table = pa.table({"c0": ["x"], "c1": ["y"]})
    with pytest.raises(ValueError, match=r"t\.csv.*not extracted"):
        sheets._check_aligned("/a/t.csv", table, [["A", "B"], ["1", "2"]], 0, [2, 2], 2)


def test_a_column_named_west_with_positive_values_is_negated_into_points():
    frame = pd.DataFrame({"LAT_NORTH": [40.1, 38.5], "LON_WEST": [111.2, 112.5]})
    warnings: list[str] = []

    gdf = sheets.points(frame, warnings)

    assert [(round(p.x, 1), round(p.y, 1)) for p in gdf.geometry] == [(-111.2, 40.1), (-112.5, 38.5)]
    assert any("west-positive and negated" in w for w in warnings)


def test_all_positive_longitudes_that_fit_utah_when_negated_are_negated():
    frame = pd.DataFrame({"Lat": [40.1, 38.5, 39.0], "Long": [111.2, None, 112.5]})
    warnings: list[str] = []

    gdf = sheets.points(frame, warnings)

    assert [round(p.x, 1) for p in gdf.geometry] == [-111.2, -112.5]
    assert any("Lat/Long read as west-positive and negated" in w for w in warnings)


def test_positive_longitudes_that_do_not_fit_utah_when_negated_stay_a_table():
    frame = pd.DataFrame({"Lat": [40.1, 38.5], "Long": [90.0, 112.5]})
    warnings: list[str] = []

    assert sheets.points(frame, warnings) is None
    assert warnings == ["coordinate columns Lat/Long found but no row falls inside Utah; table only"]


def test_swapped_latitude_and_longitude_are_reported_not_swapped():
    frame = pd.DataFrame({"Lat": [-111.2, -112.0], "Long": [40.1, 38.5]})
    warnings: list[str] = []

    assert sheets.points(frame, warnings) is None
    assert "look swapped, not changed" in warnings[0]


def test_mixed_sign_longitudes_are_left_alone():
    frame = pd.DataFrame({"Lat": [40.1, 38.5], "Long": [-111.2, 112.5]})
    warnings: list[str] = []

    gdf = sheets.points(frame, warnings)

    assert [round(p.x, 1) for p in gdf.geometry] == [-111.2]
    assert not any("negated" in w for w in warnings)
