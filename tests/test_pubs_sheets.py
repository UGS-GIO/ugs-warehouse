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
    assert "names no zone and datum" in warnings[0] and "table only" in warnings[0]
