"""pubs/vectors.py — non-spatial GeMS companion tables (DescriptionOfMapUnits,
CorrelationOfMapUnits, ...) are captured alongside the spatial vector layers, and a per-series
manifest records which extracted labels are spatial vs plain tables (ALL-5913 Task 1).
"""
from __future__ import annotations

import json
from io import BytesIO

import pytest

from ugs_warehouse.pubs import vectors

gpd = pytest.importorskip("geopandas")
pd = pytest.importorskip("pandas")
Point = pytest.importorskip("shapely.geometry").Point


def _mem_gcs(monkeypatch):
    """In-memory GCS double. `upload` reads the real local file's bytes (so a test can read back
    what `to_parquet` actually wrote); `put_bytes` stores the bytes directly."""
    store: dict[str, bytes] = {}

    def _upload(local, path, **kwargs):
        with open(local, "rb") as fh:
            store[path] = fh.read()
        return vectors.gcs.FileMeta(len(store[path]), "1220" + "00" * 32)

    monkeypatch.setattr(vectors.gcs, "upload", _upload)
    monkeypatch.setattr(vectors.gcs, "put_bytes", lambda b, p, **kwargs: store.__setitem__(p, b))
    return store


# ---------------------------------------------------------------------------
# _sources(): non-spatial GDB layers are no longer dropped, and are tagged
# ---------------------------------------------------------------------------

def test_sources_includes_non_spatial_tables_tagged_alongside_spatial_layers(monkeypatch, tmp_path):
    gdb = tmp_path / "GeologicMap.gdb"
    gdb.mkdir()
    monkeypatch.setattr(
        "pyogrio.list_layers",
        lambda path: [("ContactsAndFaults", "LineString"), ("DescriptionOfMapUnits", None)],
    )

    srcs, errors = vectors._sources(str(tmp_path))

    assert srcs == [
        (str(gdb), "ContactsAndFaults", "GeologicMap__ContactsAndFaults", True),
        (str(gdb), "DescriptionOfMapUnits", "GeologicMap__DescriptionOfMapUnits", False),
    ]
    assert errors == []


def test_sources_treats_shapefiles_as_always_spatial(tmp_path):
    (tmp_path / "faults.shp").write_bytes(b"")

    srcs, errors = vectors._sources(str(tmp_path))

    assert srcs == [(str(tmp_path / "faults.shp"), None, "faults", True)]
    assert errors == []


def test_sources_reports_a_gdb_whose_layers_cannot_be_listed(monkeypatch, tmp_path):
    (tmp_path / "Broken.gdb").mkdir()
    (tmp_path / "faults.shp").write_bytes(b"")

    def _boom(path):
        raise RuntimeError("not a file geodatabase")

    monkeypatch.setattr("pyogrio.list_layers", _boom)

    srcs, errors = vectors._sources(str(tmp_path))

    assert srcs == [(str(tmp_path / "faults.shp"), None, "faults", True)]
    assert errors == [{"source": "Broken.gdb", "error": "RuntimeError: not a file geodatabase"}]


# ---------------------------------------------------------------------------
# _extract_and_upload(): branches spatial vs table, preserves schema, writes the manifest
# ---------------------------------------------------------------------------

def test_extract_and_upload_writes_spatial_and_table_parquet_and_manifest(monkeypatch, tmp_path):
    store = _mem_gcs(monkeypatch)

    spatial_gdf = gpd.GeoDataFrame({"Type": ["fault"], "geometry": [Point(1, 2)]}, crs="EPSG:26912")
    table_df = pd.DataFrame({"MapUnit": ["Qal"], "FullName": ["Quaternary alluvium"]})
    monkeypatch.setattr("geopandas.read_file", lambda path, layer=None, engine=None: spatial_gdf)
    monkeypatch.setattr("pyogrio.read_dataframe",
                         lambda path, layer=None, read_geometry=True: table_df)

    srcs = [
        ("bundle.gdb", "ContactsAndFaults", "geo__ContactsAndFaults", True),
        ("bundle.gdb", "DescriptionOfMapUnits", "geo__DescriptionOfMapUnits", False),
    ]

    manifest = vectors._extract_and_upload(str(tmp_path), "M-100", srcs)

    assert manifest["spatial"] == ["geo__ContactsAndFaults"]
    assert manifest["tables"] == ["geo__DescriptionOfMapUnits"]
    assert manifest["errors"] == []

    spatial_path = f"{vectors.VECTORS_PREFIX}/M-100/geo__ContactsAndFaults.parquet"
    table_path = f"{vectors.VECTORS_PREFIX}/M-100/geo__DescriptionOfMapUnits.parquet"
    manifest_path = f"{vectors.VECTORS_PREFIX}/M-100/_manifest.json"
    assert store.keys() == {spatial_path, table_path, manifest_path}
    assert json.loads(store[manifest_path]) == manifest

    # Raw schema preserved verbatim: no rename/coercion, no geometry injected into the table.
    got_table = pd.read_parquet(BytesIO(store[table_path]))
    assert list(got_table.columns) == ["MapUnit", "FullName"]
    assert got_table["MapUnit"].tolist() == ["Qal"]
    assert "geometry" not in got_table.columns

    got_spatial = gpd.read_parquet(BytesIO(store[spatial_path]))
    assert list(got_spatial.columns) == ["Type", "geometry"]
    assert got_spatial.crs.to_epsg() == 26912


def test_extract_and_upload_skips_empty_layers_of_either_type(monkeypatch, tmp_path):
    store = _mem_gcs(monkeypatch)

    spatial_gdf = gpd.GeoDataFrame({"Type": ["fault"], "geometry": [Point(1, 2)]}, crs="EPSG:26912")
    table_df = pd.DataFrame({"MapUnit": ["Qal"]})
    monkeypatch.setattr("geopandas.read_file",
                         lambda path, layer=None, engine=None: spatial_gdf.iloc[0:0])
    monkeypatch.setattr("pyogrio.read_dataframe",
                         lambda path, layer=None, read_geometry=True: table_df.iloc[0:0])

    srcs = [
        ("bundle.gdb", "EmptyLines", "geo__EmptyLines", True),
        ("bundle.gdb", "EmptyTable", "geo__EmptyTable", False),
    ]

    manifest = vectors._extract_and_upload(str(tmp_path), "M-101", srcs)

    assert (manifest["spatial"], manifest["tables"]) == ([], [])
    assert manifest["empty"] == ["geo__EmptyLines", "geo__EmptyTable"]
    assert store == {}  # nothing uploaded — including no manifest — for an all-empty extraction


def test_extract_and_upload_continues_past_a_failed_layer(monkeypatch, tmp_path):
    """One layer's reader blows up; the rest still extract and the manifest reflects only
    what actually succeeded (mirrors the existing per-layer try/except)."""
    store = _mem_gcs(monkeypatch)

    table_df = pd.DataFrame({"MapUnit": ["Qal"]})

    def _boom(path, layer=None, engine=None):
        raise RuntimeError("corrupt layer")

    monkeypatch.setattr("geopandas.read_file", _boom)
    monkeypatch.setattr("pyogrio.read_dataframe",
                         lambda path, layer=None, read_geometry=True: table_df)

    srcs = [
        ("bundle.gdb", "BadLayer", "geo__BadLayer", True),
        ("bundle.gdb", "DescriptionOfMapUnits", "geo__DescriptionOfMapUnits", False),
    ]

    manifest = vectors._extract_and_upload(str(tmp_path), "M-102", srcs)

    assert (manifest["spatial"], manifest["tables"]) == ([], ["geo__DescriptionOfMapUnits"])
    assert manifest["errors"] == [{"label": "geo__BadLayer", "error": "RuntimeError: corrupt layer"}]
    # The manifest on GCS records the failure, so a missing layer is never mistaken for no layer.
    stored = json.loads(store[f"{vectors.VECTORS_PREFIX}/M-102/_manifest.json"])
    assert stored["errors"] == manifest["errors"]
    # The failed layer never reached GCS; the table that succeeded did, plus the manifest.
    assert store.keys() == {
        f"{vectors.VECTORS_PREFIX}/M-102/geo__DescriptionOfMapUnits.parquet",
        f"{vectors.VECTORS_PREFIX}/M-102/_manifest.json",
    }


def test_manifest_records_each_layers_rows_columns_crs_and_wgs84_bbox(monkeypatch, tmp_path):
    store = _mem_gcs(monkeypatch)

    # Two points in UTM zone 12N (EPSG:26912), near Salt Lake City.
    spatial_gdf = gpd.GeoDataFrame(
        {"Type": ["fault", "contact"], "geometry": [Point(424000, 4512000), Point(426000, 4514000)]},
        crs="EPSG:26912")
    table_df = pd.DataFrame({"MapUnit": ["Qal", "Tv"], "Age": [1, 30]})
    monkeypatch.setattr("geopandas.read_file", lambda path, layer=None, engine=None: spatial_gdf)
    monkeypatch.setattr("pyogrio.read_dataframe",
                         lambda path, layer=None, read_geometry=True: table_df)

    srcs = [
        ("bundle.gdb", "ContactsAndFaults", "geo__ContactsAndFaults", True),
        ("bundle.gdb", "DescriptionOfMapUnits", "geo__DescriptionOfMapUnits", False),
    ]
    vectors._extract_and_upload(str(tmp_path), "M-103", srcs)

    layers = {lay["label"]: lay for lay in
              json.loads(store[f"{vectors.VECTORS_PREFIX}/M-103/_manifest.json"])["layers"]}

    lines = layers["geo__ContactsAndFaults"]
    assert lines["spatial"] is True and lines["rows"] == 2
    assert lines["geometry_types"] == ["Point"]  # read back from the file's GeoParquet metadata
    assert lines["proj_code"] == "EPSG:26912"
    assert "NAD83 / UTM zone 12N" in lines["proj_wkt2"]  # WKT2 is recorded alongside the code
    cols = {c["name"]: c["type"] for c in lines["columns"]}
    assert cols["Type"] == "string" and cols["geometry"] == "geometry"
    west, south, east, north = lines["bbox"]
    assert -111.92 < west < east < -111.86 and 40.75 < south < north < 40.79

    # A table carries no geometry fields; its column types use the catalog's type names.
    table = layers["geo__DescriptionOfMapUnits"]
    assert table == {
        "label": "geo__DescriptionOfMapUnits", "spatial": False, "rows": 2,
        "columns": [{"name": "MapUnit", "type": "string"}, {"name": "Age", "type": "integer"}],
    }


def test_a_layer_without_a_crs_is_an_error_and_is_not_written(monkeypatch, tmp_path):
    """GeoParquet reads a missing CRS as longitude/latitude, so writing a CRS-less layer would
    mislabel its coordinates. It is reported and left out; the rest of the bundle still extracts."""
    store = _mem_gcs(monkeypatch)
    no_crs = gpd.GeoDataFrame({"Type": ["fault"], "geometry": [Point(424000, 4512000)]})
    table_df = pd.DataFrame({"MapUnit": ["Qal"]})
    monkeypatch.setattr("geopandas.read_file", lambda path, layer=None, engine=None: no_crs)
    monkeypatch.setattr("pyogrio.read_dataframe",
                         lambda path, layer=None, read_geometry=True: table_df)

    manifest = vectors._extract_and_upload(str(tmp_path), "M-104", [
        ("a.shp", None, "faults", True), ("b.gdb", "DMU", "geo__DMU", False)])

    assert manifest["spatial"] == [] and manifest["tables"] == ["geo__DMU"]
    (err,) = manifest["errors"]
    assert err["label"] == "faults" and "no CRS" in err["error"]
    assert f"{vectors.VECTORS_PREFIX}/M-104/faults.parquet" not in store


def test_a_layer_whose_geometries_are_all_null_keeps_its_rows_with_a_warning(monkeypatch, tmp_path):
    _mem_gcs(monkeypatch)
    blank = gpd.GeoDataFrame({"Type": ["fault"], "geometry": [None]}, crs="EPSG:26912")
    monkeypatch.setattr("geopandas.read_file", lambda path, layer=None, engine=None: blank)

    manifest = vectors._extract_and_upload(
        str(tmp_path), "M-107", [("a.shp", None, "faults", True)])

    assert manifest["spatial"] == ["faults"] and manifest["layers"][0]["bbox"] is None
    assert manifest["warnings"] == [
        {"label": "faults", "warning": "every geometry is null or empty; bbox not computed"}]


def test_a_bbox_that_cannot_be_computed_costs_the_bbox_not_the_layer(monkeypatch, tmp_path):
    store = _mem_gcs(monkeypatch)
    gdf = gpd.GeoDataFrame({"Type": ["fault"], "geometry": [Point(424000, 4512000)]},
                           crs="EPSG:26912")
    monkeypatch.setattr("geopandas.read_file", lambda path, layer=None, engine=None: gdf)

    def _no_path(*args, **kwargs):
        raise RuntimeError("no transformation path")

    monkeypatch.setattr("pyproj.Transformer.from_crs", _no_path)

    manifest = vectors._extract_and_upload(
        str(tmp_path), "M-108", [("a.shp", None, "faults", True)])

    assert manifest["errors"] == [] and manifest["layers"][0]["bbox"] is None
    assert manifest["warnings"] == [
        {"label": "faults", "warning": "bbox not computed: RuntimeError: no transformation path"}]
    assert f"{vectors.VECTORS_PREFIX}/M-108/faults.parquet" in store


def test_a_bbox_outside_the_crs_area_of_use_warns_that_the_prj_may_be_wrong(monkeypatch, tmp_path):
    """Coordinates far east of UTM zone 12N's band (114W-108W) labelled 26912: a likely wrong .prj."""
    _mem_gcs(monkeypatch)
    gdf = gpd.GeoDataFrame({"Type": ["fault"], "geometry": [Point(1_500_000, 4_512_000)]},
                           crs="EPSG:26912")
    monkeypatch.setattr("geopandas.read_file", lambda path, layer=None, engine=None: gdf)

    manifest = vectors._extract_and_upload(
        str(tmp_path), "M-109", [("a.shp", None, "faults", True)])

    (warning,) = manifest["warnings"]
    assert "outside the CRS's area of use" in warning["warning"]
    assert manifest["layers"][0]["bbox"] is not None  # still recorded; the warning flags it


def test_extracted_series_leaves_out_a_series_whose_manifest_lists_errors(monkeypatch):
    """A partly failed pub is retried and reported on every run, not skipped as done."""
    pfx = vectors.VECTORS_PREFIX
    bodies = {f"{pfx}/M-1DM/_manifest.json": b'{"spatial": ["a"], "errors": []}',
              f"{pfx}/M-2DM/_manifest.json": b'{"spatial": ["a"], "errors": [{"label": "b"}]}',
              f"{pfx}/M-3DM/_manifest.json": b'{"spatial": ["a"], "tables": []}'}
    monkeypatch.setattr(vectors.gcs, "list_paths",
                        lambda prefix: [*bodies, f"{pfx}/M-2DM/a.parquet"])
    monkeypatch.setattr(vectors.gcs, "get_bytes", lambda p: bodies[p])

    assert vectors.extracted_series() == {"M-1DM", "M-3DM"}


def _stub_bundle(monkeypatch, tmp_path, srcs_and_errors):
    """extract_one with the download and unzip stubbed out and _sources returning a fixed answer."""
    from ugs_warehouse.pubs import harvest

    monkeypatch.setattr(harvest, "zip_urls", lambda sid: (None, "https://x/gis.zip"))
    monkeypatch.setattr(harvest, "download", lambda url, dst: open(dst, "wb").close())
    monkeypatch.setattr(vectors.zipfile, "ZipFile", lambda p: _NoopZip())
    monkeypatch.setattr(vectors, "_sources", lambda work: srcs_and_errors)
    monkeypatch.setattr(vectors.gcs, "list_paths", lambda prefix: [])


class _NoopZip:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extractall(self, path):
        pass


def test_extract_one_fails_when_any_layer_fails_but_keeps_the_rest(monkeypatch, tmp_path):
    store = _mem_gcs(monkeypatch)
    table_df = pd.DataFrame({"MapUnit": ["Qal"]})

    def _boom(path, layer=None, engine=None):
        raise RuntimeError("corrupt layer")

    monkeypatch.setattr("geopandas.read_file", _boom)
    monkeypatch.setattr("pyogrio.read_dataframe",
                         lambda path, layer=None, read_geometry=True: table_df)
    _stub_bundle(monkeypatch, tmp_path, ([
        ("b.gdb", "BadLayer", "geo__BadLayer", True),
        ("b.gdb", "DescriptionOfMapUnits", "geo__DescriptionOfMapUnits", False),
    ], []))

    assert vectors.extract_one("M-105", force=True) == "fail:layers"
    assert f"{vectors.VECTORS_PREFIX}/M-105/geo__DescriptionOfMapUnits.parquet" in store


def test_extract_one_fails_when_a_gdb_cannot_be_listed_and_nothing_else_exists(monkeypatch, tmp_path):
    store = _mem_gcs(monkeypatch)
    _stub_bundle(monkeypatch, tmp_path, ([], [{"source": "Broken.gdb", "error": "RuntimeError: x"}]))

    assert vectors.extract_one("M-106", force=True) == "fail:layers"
    assert store == {}  # nothing extracted, so no manifest: the next run retries and fails again


def test_a_batch_run_visits_only_new_pubs_with_a_gis_zip(monkeypatch):
    """--all lists the extracted series once and skips them, and never looks up a pub that has no
    GIS zip in its attachments, so a weekly run with nothing new downloads nothing."""
    import sys

    from ugs_warehouse.pubs import harvest, source, vectors

    monkeypatch.setattr(source, "read_pubs", lambda: [
        {"series_id": "M-1DM"}, {"series_id": "M-2DM"}, {"series_id": "M-3"}])
    gis = {"M-1DM": "https://x/m1-gis.zip", "M-2DM": "https://x/m2-gis.zip"}
    monkeypatch.setattr(harvest, "_get_attached_zips", lambda sid: (None, gis.get(sid)))
    listings: list[str] = []

    def list_paths(prefix):
        listings.append(prefix)
        return [f"{vectors.VECTORS_PREFIX}/M-1DM/_manifest.json"]

    monkeypatch.setattr(vectors.gcs, "list_paths", list_paths)
    monkeypatch.setattr(vectors.gcs, "get_bytes", lambda p: b'{"spatial": ["a"], "errors": []}')
    downloads: list[str] = []
    monkeypatch.setattr(harvest, "download", lambda url, dst: downloads.append(url) or 1 / 0)
    monkeypatch.setattr(sys, "argv", ["vectors", "--all"])

    vectors.main()

    assert downloads == ["https://x/m2-gis.zip"]
    assert listings == [f"{vectors.VECTORS_PREFIX}/"]
