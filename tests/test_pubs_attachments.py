"""pubs/vectors.py extracts every data attachment of a publication: zips of any label, bare
spreadsheets and CSV files, with the source file recorded per layer."""
from __future__ import annotations

import json
import os
import shutil
import sys
import zipfile

import pytest
from test_pubs_vectors import _gis_zips, _mem_gcs, no_identify  # noqa: F401 (autouse fixture)

from ugs_warehouse.pubs import harvest, identity, vectors

gpd = pytest.importorskip("geopandas")
pd = pytest.importorskip("pandas")
openpyxl = pytest.importorskip("openpyxl")
Point = pytest.importorskip("shapely.geometry").Point

PFX = vectors.VECTORS_PREFIX


def _shapefile(folder, name="faults", **columns):
    folder.mkdir(parents=True, exist_ok=True)
    gdf = gpd.GeoDataFrame({"Name": ["a"], **columns}, geometry=[Point(-111.5, 40.5)],
                           crs="EPSG:4326")
    gdf.to_file(folder / f"{name}.shp")
    return gdf


def _zip(path, folder, extra=None):
    """Zip the files in `folder`, plus `extra` {member name: bytes}."""
    with zipfile.ZipFile(path, "w") as z:
        for f in sorted(os.listdir(folder)):
            z.write(folder / f, f)
        for name, body in (extra or {}).items():
            z.writestr(name, body)
    return path


def _workbook(path, rows, sheet="Sheet1"):
    wb = openpyxl.Workbook()
    wb.active.title = sheet
    for row in rows:
        wb.active.append(row)
    wb.save(path)
    return path


def _serve(monkeypatch, *attachments):
    """Stub the attachments a pub lists and their downloads. Each is (title, local file)."""
    files, local = [], {}
    for title, path in attachments:
        url = f"https://x/{os.path.basename(path)}"
        local[url] = path
        files.append(harvest.DataFile(identity.attachment_key(title), title, url))
    monkeypatch.setattr(harvest, "data_files", lambda sid: files)
    monkeypatch.setattr(harvest, "download", lambda url, dst: shutil.copy(local[url], dst))


def _manifest(store, sid):
    return json.loads(store[f"{PFX}/{sid}/_manifest.json"])


def test_a_zip_labelled_data_is_extracted_and_records_its_source_file(monkeypatch, tmp_path):
    store = _mem_gcs(monkeypatch)
    _shapefile(tmp_path / "src")
    _serve(monkeypatch, ("Data - Zip", _zip(tmp_path / "d.zip", tmp_path / "src")))

    assert vectors.extract_one("M-300", force=True) == "ok"

    m = _manifest(store, "M-300")
    (layer,) = m["layers"]
    assert layer["label"] == "faults" and layer["spatial"] is True
    assert layer["source_file"] == {"key": "data_zip", "title": "Data - Zip", "path": "faults.shp"}
    assert m["extract_version"] == vectors.EXTRACT_VERSION
    assert f"{PFX}/M-300/faults.parquet" in store


def test_macos_resource_forks_in_a_zip_are_skipped_not_read_as_layers(monkeypatch, tmp_path):
    store = _mem_gcs(monkeypatch)
    _shapefile(tmp_path / "src")
    junk = {"__MACOSX/._faults.shp": b"\x00\x05\x16\x07junk", "__MACOSX/faults.shp": b"junk",
            "._faults.dbf": b"junk"}
    _serve(monkeypatch, ("Data", _zip(tmp_path / "d.zip", tmp_path / "src", junk)))

    assert vectors.extract_one("M-301", force=True) == "ok"

    m = _manifest(store, "M-301")
    assert [lay["label"] for lay in m["layers"]] == ["faults"] and m["errors"] == []


def test_an_xlsx_with_coordinates_gives_a_table_and_a_point_layer(monkeypatch, tmp_path):
    store = _mem_gcs(monkeypatch)
    f = _workbook(tmp_path / "Samples.xlsx", [
        ["Table 1. Samples"], [],
        ["Sample", "Latitude", "Longitude", "Depth"],
        ["A-1", 40.1, -111.2, 12], ["A-2", 40.2, -111.3, 15.5], ["A-3", None, None, 3]])
    _serve(monkeypatch, ("Appendix A - Excel", f))

    assert vectors.extract_one("M-302", force=True) == "ok"

    m = _manifest(store, "M-302")
    layers = {lay["label"]: lay for lay in m["layers"]}
    assert set(layers) == {"Samples__Sheet1", "Samples__Sheet1_points"}
    table, points = layers["Samples__Sheet1"], layers["Samples__Sheet1_points"]
    assert table["spatial"] is False and table["rows"] == 3
    assert [c["name"] for c in table["columns"]] == ["Sample", "Latitude", "Longitude", "Depth"]
    assert points["spatial"] is True and points["rows"] == 2 and points["proj_code"] == "EPSG:4326"
    assert points["pmtiles"] is True and f"{PFX}/M-302/Samples__Sheet1_points.pmtiles" in store
    for lay in layers.values():
        assert lay["source_file"] == {"key": "appendix_a_excel", "title": "Appendix A - Excel",
                                      "path": "Samples.xlsx"}
        assert lay["source"] == "Samples"
    (warn,) = [w for w in m["warnings"] if "left out of the point layer" in w["warning"]]
    assert warn["label"] == "Samples__Sheet1" and "1 of 3 rows" in warn["warning"]


def test_an_xlsx_without_coordinates_gives_a_table_only(monkeypatch, tmp_path):
    store = _mem_gcs(monkeypatch)
    f = _workbook(tmp_path / "Chem.xlsx", [["Sample", "SiO2"], ["A-1", 74.1], ["A-2", 61.4]])
    _serve(monkeypatch, ("Data", f))

    assert vectors.extract_one("M-303", force=True) == "ok"

    m = _manifest(store, "M-303")
    assert m["tables"] == ["Chem__Sheet1"] and m["spatial"] == []
    assert not [k for k in store if k.endswith(".pmtiles")]


def test_a_sheet_with_no_header_row_is_a_warning_and_the_others_still_extract(monkeypatch, tmp_path):
    store = _mem_gcs(monkeypatch)
    wb = openpyxl.Workbook()
    wb.active.title = "Numbers"
    for row in ([1, 2], [3, 4]):
        wb.active.append(row)
    wb.create_sheet("Good").append(["A", "B"])
    wb["Good"].append([1, 2])
    wb.save(tmp_path / "w.xlsx")
    _serve(monkeypatch, ("Data", str(tmp_path / "w.xlsx")))

    assert vectors.extract_one("M-304", force=True) == "ok"

    m = _manifest(store, "M-304")
    assert m["tables"] == ["w__Good"] and m["errors"] == []
    assert any(w["label"] == "w__Numbers" and "no header row" in w["warning"] for w in m["warnings"])


def test_a_csv_attachment_is_a_table(monkeypatch, tmp_path):
    store = _mem_gcs(monkeypatch)
    p = tmp_path / "samples.csv"
    p.write_text("Sample,Depth\nA-1,12\nA-2,15.5\n")
    _serve(monkeypatch, ("Samples - CSV", str(p)))

    assert vectors.extract_one("M-305", force=True) == "ok"

    (layer,) = _manifest(store, "M-305")["layers"]
    assert layer["label"] == "samples" and layer["rows"] == 2
    assert layer["source_file"]["path"] == "samples.csv"
    assert [c["type"] for c in layer["columns"]] == ["string", "number"]


def test_several_attachments_share_one_run_with_unique_labels_and_their_own_source_file(
        monkeypatch, tmp_path):
    store = _mem_gcs(monkeypatch)
    _shapefile(tmp_path / "a")
    _shapefile(tmp_path / "b")
    _serve(monkeypatch,
           ("GIS Data - Zip", _zip(tmp_path / "gis.zip", tmp_path / "a")),
           ("Appendix A", _zip(tmp_path / "appx.zip", tmp_path / "b")))

    assert vectors.extract_one("M-306", force=True) == "ok"

    layers = {lay["label"]: lay for lay in _manifest(store, "M-306")["layers"]}
    assert set(layers) == {"faults", "faults_2"}
    assert layers["faults"]["source_file"]["key"] == "gis_data_zip"
    assert layers["faults_2"]["source_file"]["key"] == "appendix_a"
    assert f"{PFX}/M-306/faults.parquet" in store and f"{PFX}/M-306/faults_2.parquet" in store


def test_the_unzip_limit_covers_all_of_a_pubs_zips(monkeypatch, tmp_path):
    store = _mem_gcs(monkeypatch)
    _shapefile(tmp_path / "a")
    _shapefile(tmp_path / "b")
    one = _zip(tmp_path / "one.zip", tmp_path / "a")
    two = _zip(tmp_path / "two.zip", tmp_path / "b")
    with zipfile.ZipFile(one) as z:
        size = sum(i.file_size for i in z.infolist())
    monkeypatch.setattr(vectors, "MAX_UNZIPPED_BYTES", size + size // 2)
    _serve(monkeypatch, ("First", one), ("Second", two))

    assert vectors.extract_one("M-307", force=True) == "fail:layers"

    m = _manifest(store, "M-307")
    assert [lay["label"] for lay in m["layers"]] == ["faults"]
    (err,) = m["errors"]
    assert err["source"] == "Second" and "limit" in err["error"]


def test_columns_that_repeat_ignoring_case_are_renamed_with_a_warning(monkeypatch, tmp_path):
    _mem_gcs(monkeypatch)
    frame = gpd.GeoDataFrame({"Name": ["a"], "NAME": ["b"], "Age": [1]},
                             geometry=[Point(-111.5, 40.5)], crs="EPSG:4326")
    monkeypatch.setattr("geopandas.read_file", lambda path, layer=None, engine=None: frame)

    manifest = vectors._extract_and_upload(
        str(tmp_path), "M-308", [("dup.shp", None, "dup", True)])

    (layer,) = manifest["layers"]
    assert [c["name"] for c in layer["columns"] if c["type"] != "geometry"][:3] == [
        "Name", "NAME_2", "Age"]
    (warn,) = [w for w in manifest["warnings"] if w.get("label") == "dup"]
    assert "NAME -> NAME_2" in warn["warning"]


def test_a_shapefile_that_is_not_utf8_is_read_as_cp1252_with_a_warning(monkeypatch, tmp_path):
    store = _mem_gcs(monkeypatch)
    folder = tmp_path / "src"
    folder.mkdir()
    gdf = gpd.GeoDataFrame({"Name": ["Caf\xe9"]}, geometry=[Point(-111.5, 40.5)], crs="EPSG:4326")
    gdf.to_file(folder / "cafes.shp", encoding="cp1252")
    (folder / "cafes.cpg").write_text("UTF-8")  # claims UTF-8, holds cp1252 bytes
    _serve(monkeypatch, ("Data", _zip(tmp_path / "d.zip", folder)))

    assert vectors.extract_one("M-309", force=True) == "ok"

    m = _manifest(store, "M-309")
    (warn,) = [w for w in m["warnings"] if w.get("code") == vectors.ENCODING_FALLBACK]
    assert warn["label"] == "cafes" and "cp1252" in warn["warning"]
    assert [lay["label"] for lay in m["layers"]] == ["cafes"] and m["errors"] == []


def test_any_other_shapefile_read_error_still_fails_the_layer(monkeypatch, tmp_path):
    _mem_gcs(monkeypatch)

    def boom(path, layer=None, engine=None, **kw):
        raise RuntimeError("corrupt dbf")

    monkeypatch.setattr("geopandas.read_file", boom)

    manifest = vectors._extract_and_upload(str(tmp_path), "M-310", [("x.shp", None, "x", True)])

    assert manifest["errors"] == [{"label": "x", "error": "RuntimeError: corrupt dbf"}]


# ---- which attachments a run visits, and when a pub is extracted again ----

def test_data_files_are_every_non_geotiff_zip_and_spreadsheet_with_the_gis_zip_first(monkeypatch):
    rows = [
        {"series_id": "DS-5", "pub_url": "ds-5/ds-5_geotiff.zip", "extra_data": "GeoTIFF - Zip"},
        {"series_id": "DS-5", "pub_url": "ds-5/ds-5_data.zip", "extra_data": "Data - Zip"},
        {"series_id": "DS-5", "pub_url": "ds-5/ds-5_gis.zip", "extra_data": "GIS - Zip"},
        {"series_id": "DS-5", "pub_url": "ds-5/appx.xlsx", "extra_data": "Appendix A - Excel"},
        {"series_id": "DS-5", "pub_url": "ds-5/old.xls", "extra_data": ""},
        {"series_id": "DS-5", "pub_url": "ds-5/ds-5.pdf", "extra_data": "Text - PDF"},
        {"series_id": "M-1", "pub_url": "m-1.zip", "extra_data": "Data"}]
    harvest._attachments_cache.clear()
    monkeypatch.setattr("ugs_warehouse.pubs.source.read_attachments", lambda: rows)

    files = harvest.data_files("DS-5")

    base = "https://ugspub.nr.utah.gov/publications/ds-5/"
    assert [(f.key, f.title, f.url) for f in files] == [
        ("gis_zip", "GIS - Zip", base + "ds-5_gis.zip"),
        ("appendix_a_excel", "Appendix A - Excel", base + "appx.xlsx"),   # URL order after the GIS zip
        ("data_zip", "Data - Zip", base + "ds-5_data.zip"),
        ("file", "old.xls", base + "old.xls")]
    harvest._attachments_cache.clear()


def _all_run(monkeypatch, manifests: dict[str, dict], attempts: dict[str, dict] | None = None):
    """An `--all` run over M-1DM..M-3DM, each with a data zip; `manifests` and `attempts` are what
    GCS already holds. Returns the URLs it tried to download, in order."""
    from ugs_warehouse.pubs import source

    docs = {f"{PFX}/{s}/_manifest.json": m for s, m in manifests.items()}
    docs.update({f"{PFX}/{s}/_attempt.json": a for s, a in (attempts or {}).items()})
    monkeypatch.setattr(source, "read_pubs", lambda: [{"series_id": f"M-{i}DM"} for i in (1, 2, 3)])
    _gis_zips(monkeypatch, lambda sid: (None, f"https://x/{sid}.zip"))
    monkeypatch.setattr(vectors.gcs, "list_paths", lambda prefix: [p for p in docs if p.startswith(prefix)])
    monkeypatch.setattr(vectors.gcs, "get_bytes", lambda p: json.dumps(docs[p]).encode())
    monkeypatch.setattr(vectors.gcs, "put_bytes", lambda b, p, **kw: None)
    downloads: list[str] = []
    monkeypatch.setattr(harvest, "download", lambda url, dst: downloads.append(url) or 1 / 0)
    monkeypatch.setattr(sys, "argv", ["vectors", "--all"])
    vectors.main()
    return downloads


def _clean(**kw):
    return {"spatial": ["a"], "layers": [], "errors": [], **kw}


def test_a_clean_manifest_below_the_current_extract_version_is_extracted_again(monkeypatch):
    downloads = _all_run(monkeypatch, {
        "M-1DM": _clean(extract_version=1),                              # the old coverage
        "M-2DM": _clean(extract_version=vectors.EXTRACT_VERSION)})       # already current

    # M-3DM was never tried and goes first; the upgrade of M-1DM follows; M-2DM is skipped.
    assert downloads == ["https://x/M-3DM.zip", "https://x/M-1DM.zip"]


def test_a_manifest_with_no_extract_version_counts_as_the_old_coverage():
    assert not vectors.is_current(_clean())
    assert not vectors.is_current(_clean(extract_version=vectors.EXTRACT_VERSION - 1))
    assert vectors.is_current(_clean(extract_version=vectors.EXTRACT_VERSION))
    assert not vectors.is_current(_clean(extract_version=vectors.EXTRACT_VERSION, errors=[{"x": 1}]))


def test_a_stale_manifest_is_not_healed_it_waits_for_re_extraction(monkeypatch):
    healed: list[str] = []
    monkeypatch.setattr(vectors, "self_heal", lambda needs, **kw: healed.extend(needs) or vectors.Heal())

    _all_run(monkeypatch, {"M-1DM": _clean(extract_version=1)})

    assert healed == []


def test_a_manifest_with_errors_is_extracted_again_and_not_healed_from_its_stale_needs(monkeypatch):
    healed: list[str] = []
    monkeypatch.setattr(vectors, "self_heal", lambda needs, **kw: healed.extend(needs) or vectors.Heal())
    partial = _clean(extract_version=vectors.EXTRACT_VERSION, errors=[{"label": "b", "error": "x"}])

    downloads = _all_run(monkeypatch, {"M-1DM": partial})

    assert healed == []
    assert "https://x/M-1DM.zip" in downloads


def test_an_unsupported_pub_is_healed_from_its_readable_layers_without_a_download(monkeypatch):
    healed: list[str] = []
    monkeypatch.setattr(vectors, "self_heal", lambda needs, **kw: healed.extend(needs) or vectors.Heal())
    errors = [{"source": "x.mpk", "error": "unsupported format"}]
    partial = _clean(extract_version=vectors.EXTRACT_VERSION, errors=errors)
    unsupported = {"result": "unsupported", "extract_version": vectors.EXTRACT_VERSION, "errors": errors}

    downloads = _all_run(monkeypatch, {"M-1DM": partial}, {"M-1DM": unsupported})

    assert healed == ["M-1DM"]
    assert "https://x/M-1DM.zip" not in downloads


def test_a_nothing_to_extract_attempt_from_the_old_coverage_is_tried_again(monkeypatch):
    skip = {"result": "skip"}
    downloads = _all_run(monkeypatch, {}, {
        "M-1DM": {**skip},                                          # no extract_version: old
        "M-2DM": {**skip, "extract_version": vectors.EXTRACT_VERSION}})

    assert sorted(downloads) == ["https://x/M-1DM.zip", "https://x/M-3DM.zip"]


# ---- repeated and colliding attachments ----

def _feed(monkeypatch, rows):
    harvest._attachments_cache.clear()
    monkeypatch.setattr("ugs_warehouse.pubs.source.read_attachments", lambda: rows)


def test_an_attachment_the_feed_lists_twice_is_one_file(monkeypatch):
    row = {"series_id": "OFR-745", "pub_url": "ofr-745/ofr-745.zip", "extra_data": "Data - Zip"}
    _feed(monkeypatch, [row, dict(row)])

    assert len(harvest.data_files("OFR-745")) == 1
    harvest._attachments_cache.clear()


def test_attachment_keys_are_unique_stable_and_leave_the_fixed_keys_alone():
    rows = [("https://x/b.zip", "GeoTIFF - Zip"), ("https://x/a.zip", "GeoTIFF - Zip"),
            ("https://x/c.pdf", "Publication"), ("https://x/a.zip", "GeoTIFF - Zip"),
            ("https://x/d.zip", "geotiff zip")]

    keys = identity.attachment_keys(rows)

    # numbered in (key, url) order, so the feed's row order cannot change them
    assert keys == {"https://x/a.zip": "geotiff_zip", "https://x/b.zip": "geotiff_zip_2",
                    "https://x/d.zip": "geotiff_zip_3", "https://x/c.pdf": "publication_2"}
    assert identity.attachment_keys(list(reversed(rows))) == keys


def test_every_same_labelled_attachment_gets_its_own_asset_and_the_vectors_key_matches(monkeypatch):
    from ugs_warehouse.pubs import sink_stac

    rows = [{"series_id": "CI-82", "pub_url": f"ci-82/{n}.zip", "extra_data": "GeoTIFF - Zip"}
            for n in ("b", "a")] + [
        {"series_id": "CI-82", "pub_url": "ci-82/x.zip", "extra_data": "Data"}]
    _feed(monkeypatch, rows)
    pub = {"series_id": "CI-82", "pub_name": "A map", "pub_year": "2020",
           "pub_url": "https://ugspub.nr.utah.gov/publications/ci-82/ci-82.pdf",
           "series": "Circular", "pub_publisher": "Utah Geological Survey"}
    foot = {"type": "Polygon", "coordinates": [[[-112.5, 40.5], [-112.0, 40.5], [-112.0, 41.0],
                                                 [-112.5, 41.0], [-112.5, 40.5]]]}
    coll, _ = sink_stac.build_publication(
        pub, rows, geom=foot, bbox=[-112.5, 40.5, -112.0, 41.0], fp_source="cog", has_cog=False,
        has_cover=False, vector_layers=[], companion_tables=[], layers={}, registered_dois=frozenset())

    zips = {k: a["href"].rsplit("/", 1)[-1] for k, a in coll["assets"].items()
            if a["href"].endswith(".zip")}
    assert zips == {"geotiff_zip": "a.zip", "geotiff_zip_2": "b.zip", "data": "x.zip"}
    assert harvest.data_files("CI-82")[0].key == "data"
    harvest._attachments_cache.clear()


def test_a_mac_zip_of_a_geodatabase_still_unpacks_as_the_geodatabase(tmp_path):
    inner = tmp_path / "Map.gdb.zip"
    with zipfile.ZipFile(inner, "w") as z:
        z.writestr("a00000001.gdbtable", b"x")
        z.writestr("__MACOSX/._a00000001.gdbtable", b"junk")
    work = tmp_path / "w"
    work.mkdir()
    shutil.copy(inner, work / "Map.gdb.zip")

    errors, dirs = vectors._unzip_nested(str(work), remaining=2**20)

    assert errors == [] and dirs == [str(work / "Map.gdb")]
    assert (work / "Map.gdb" / "a00000001.gdbtable").exists()


def test_a_pub_that_ends_as_skip_keeps_its_warnings_on_the_attempt_record(monkeypatch, tmp_path):
    store = _mem_gcs(monkeypatch)
    f = _workbook(tmp_path / "gravity.xlsx", [[1, 2], [3, 4]])
    _serve(monkeypatch, ("Data", f))

    assert vectors.extract_one("MP-13-2", force=True) == "skip"

    attempt = json.loads(store[f"{PFX}/MP-13-2/_attempt.json"])
    assert attempt["result"] == "skip"
    assert "no header row" in attempt["warnings"][0]["warning"]


def test_the_fallback_zip_has_no_source_key_when_the_feed_lists_no_such_asset(monkeypatch, tmp_path):
    store = _mem_gcs(monkeypatch)
    _shapefile(tmp_path / "src")
    z = _zip(tmp_path / "gis.zip", tmp_path / "src")
    monkeypatch.setattr(harvest, "data_files", lambda sid: [])
    monkeypatch.setattr(harvest, "zip_urls", lambda sid: (None, "https://x/gis.zip"))
    monkeypatch.setattr(harvest, "asset_key_of", lambda sid, url: None)
    monkeypatch.setattr(harvest, "download", lambda url, dst: shutil.copy(z, dst))

    assert vectors.extract_one("M-320", force=True) == "ok"

    (layer,) = _manifest(store, "M-320")["layers"]
    assert layer["source_file"] == {"title": "GIS", "path": "faults.shp"}


def test_a_url_listed_under_two_labels_gets_the_same_title_whatever_the_feed_order(monkeypatch):
    from ugs_warehouse.pubs import sink_stac

    rows = [{"series_id": "OFR-745", "pub_url": "ofr-745/d.zip", "extra_data": label}
            for label in ("Data - Zip", "Appendix")]
    pub = {"series_id": "OFR-745", "pub_name": "A map", "pub_year": "2020",
           "pub_url": "https://ugspub.nr.utah.gov/publications/ofr-745/ofr-745.pdf",
           "series": "Open File Report", "pub_publisher": "Utah Geological Survey"}
    foot = {"type": "Polygon", "coordinates": [[[-112.5, 40.5], [-112.0, 40.5], [-112.0, 41.0],
                                                 [-112.5, 41.0], [-112.5, 40.5]]]}
    seen = set()
    for order in (rows, rows[::-1]):
        _feed(monkeypatch, order)
        coll, _ = sink_stac.build_publication(
            pub, order, geom=foot, bbox=[-112.5, 40.5, -112.0, 41.0], fp_source="cog",
            has_cog=False, has_cover=False, vector_layers=[], companion_tables=[], layers={}, registered_dois=frozenset())
        (zip_asset,) = [(k, a["title"]) for k, a in coll["assets"].items()
                        if a["href"].endswith(".zip")]
        (file,) = harvest.data_files("OFR-745")
        assert (file.key, file.title) == zip_asset
        seen.add(zip_asset)
    assert seen == {("appendix", "Appendix")}
    harvest._attachments_cache.clear()
