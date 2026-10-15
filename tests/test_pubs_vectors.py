"""pubs/vectors.py — non-spatial GeMS companion tables (DescriptionOfMapUnits,
CorrelationOfMapUnits, ...) are captured alongside the spatial vector layers, and a per-series
manifest records which extracted labels are spatial vs plain tables (ALL-5913 Task 1).
"""
from __future__ import annotations

import json
import os
from io import BytesIO

import pytest

from ugs_warehouse.pubs import vectors

gpd = pytest.importorskip("geopandas")
pd = pytest.importorskip("pandas")
Point = pytest.importorskip("shapely.geometry").Point


@pytest.fixture(autouse=True)
def no_identify(monkeypatch):
    """The identify layers read the mosaic membership and footprints; their tests are in
    test_pubs_identify.py."""
    monkeypatch.setattr(vectors.identify, "build_changed", lambda tiers=None: vectors.identify.Result())


@pytest.fixture
def no_geopackage(monkeypatch):
    """For tests that run extraction or the heal over manifests with no GeoParquet behind them: the
    GeoPackage build is out of the picture. Its own tests are in test_pubs_geopackage.py."""
    monkeypatch.setattr(vectors, "needs_geopackage", lambda manifest: False)
    monkeypatch.setattr(vectors, "_geopackage_series", lambda series_id: (False, []))


def _fake_tippecanoe(geojsonl, pmtiles, layer, *, id_attr=None, what=None, extra_opts=None):
    with open(pmtiles, "wb") as fh:
        fh.write(f"tiles:{layer}".encode())


def _mem_gcs(monkeypatch):
    """In-memory GCS double. `upload` reads the real local file's bytes (so a test can read back
    what `to_parquet` actually wrote); `put_bytes` stores the bytes directly. tippecanoe is
    stubbed, so tests never need the binary, and so is `ogrinfo` (no declared relationships)."""
    store: dict[str, bytes] = {}
    monkeypatch.setattr(vectors.sink_pmtiles, "run_tippecanoe", _fake_tippecanoe)
    monkeypatch.setattr(vectors.relations, "read_declared", lambda gdb: ([], None))

    def _upload(local, path, **kwargs):
        with open(local, "rb") as fh:
            store[path] = fh.read()
        return vectors.gcs.FileMeta(len(store[path]), "1220" + "00" * 32)

    monkeypatch.setattr(vectors.gcs, "upload", _upload)
    monkeypatch.setattr(vectors.gcs, "put_bytes", lambda b, p, **kwargs: store.__setitem__(p, b))
    monkeypatch.setattr(vectors.gcs, "open_reader", lambda path: BytesIO(store[path]))
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
    tiles_path = f"{vectors.VECTORS_PREFIX}/M-100/geo__ContactsAndFaults.pmtiles"
    table_path = f"{vectors.VECTORS_PREFIX}/M-100/geo__DescriptionOfMapUnits.parquet"
    manifest_path = f"{vectors.VECTORS_PREFIX}/M-100/_manifest.json"
    style_path = f"{vectors.VECTORS_PREFIX}/M-100/geo__ContactsAndFaults.style.json"
    assert store.keys() == {spatial_path, tiles_path, style_path, table_path, manifest_path}
    assert json.loads(store[manifest_path]) == manifest
    assert manifest["layers"][0]["style"] == "geo__ContactsAndFaults.style.json"

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
    assert table.pop("bytes") > 0 and table.pop("checksum")  # the GeoParquet's size and hash
    assert table == {
        "label": "geo__DescriptionOfMapUnits", "spatial": False, "rows": 2,
        "columns": [{"name": "MapUnit", "type": "string"}, {"name": "Age", "type": "integer"}],
        "source": "geo", "kind": "supporting",
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
        {"label": "faults", "warning": "every geometry is null or empty; bbox not computed"},
        {"label": "faults", "code": vectors.NO_GEOMETRY,
         "warning": "every geometry is null or empty; no PMTiles built"}]
    assert "pmtiles" not in manifest["layers"][0]


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
    bodies = {f"{pfx}/M-1DM/_manifest.json": b'{"spatial": ["a"], "layers": [], "errors": []}',
              f"{pfx}/M-2DM/_manifest.json": b'{"spatial": ["a"], "layers": [], "errors": [{"label": "b"}]}',
              f"{pfx}/M-3DM/_manifest.json": b'{"spatial": ["a"], "layers": []}'}
    monkeypatch.setattr(vectors.gcs, "list_paths",
                        lambda prefix: [*bodies, f"{pfx}/M-2DM/a.parquet"])
    monkeypatch.setattr(vectors.gcs, "get_bytes", lambda p: bodies[p])

    assert vectors.extracted_series() == {"M-1DM", "M-3DM"}


def test_an_old_format_manifest_is_re_extracted_by_an_ordinary_run(monkeypatch):
    """A manifest written before per-layer metadata was recorded has no `layers` list. Counting
    it as not done lets resumable runs upgrade every pub, since a --force run restarts from the
    first pub on each attempt and cannot finish within the job timeout."""
    pfx = vectors.VECTORS_PREFIX
    bodies = {f"{pfx}/M-1DM/_manifest.json": b'{"spatial": ["a"], "tables": []}',
              f"{pfx}/M-2DM/_manifest.json": b'{"spatial": ["a"], "layers": [], "errors": []}'}
    monkeypatch.setattr(vectors.gcs, "list_paths", lambda prefix: list(bodies))
    monkeypatch.setattr(vectors.gcs, "get_bytes", lambda p: bodies[p])

    assert vectors.extracted_series() == {"M-2DM"}


def test_extracted_series_retries_a_corrupt_manifest_or_a_series_with_no_manifest(monkeypatch, capsys):
    """One unreadable manifest is reported and re-extracted, not fatal to the batch; layers with
    no manifest (its write failed) are not counted as done."""
    pfx = vectors.VECTORS_PREFIX
    bodies = {f"{pfx}/M-1DM/_manifest.json": b'{"spatial": ["a"], "layers": []}',
              f"{pfx}/M-2DM/_manifest.json": b"not json",
              f"{pfx}/M-4DM/_manifest.json": b"null"}
    monkeypatch.setattr(vectors.gcs, "list_paths",
                        lambda prefix: [*bodies, f"{pfx}/M-3DM/a.parquet"])
    monkeypatch.setattr(vectors.gcs, "get_bytes", lambda p: bodies[p])

    assert vectors.extracted_series() == {"M-1DM"}
    err = capsys.readouterr().err
    assert f"{pfx}/M-2DM/_manifest.json" in err and f"{pfx}/M-4DM/_manifest.json" in err


def test_a_forced_run_never_reads_the_existing_manifests(monkeypatch, tmp_path):
    _mem_gcs(monkeypatch)
    _stub_bundle(monkeypatch, tmp_path, ([], []))
    monkeypatch.setattr(vectors.gcs, "get_bytes", lambda p: 1 / 0)
    monkeypatch.setattr(vectors, "extracted_series", lambda: 1 / 0)

    assert vectors.extract_one("M-110", force=True) == "skip"  # empty bundle, no manifest reads


def _gis_zips(monkeypatch, zips):
    """The pubs a batch run lists: `zips(sid)` is `(None, gis_url)`, as `_get_attached_zips`
    answered before attachments of any label were extracted."""
    from ugs_warehouse.pubs import harvest

    monkeypatch.setattr(harvest, "_get_attached_zips", zips)
    monkeypatch.setattr(harvest, "data_files", lambda sid: (
        [harvest.DataFile("gis", "GIS", url)] if (url := zips(sid)[1]) else []))


def _stub_bundle(monkeypatch, tmp_path, srcs_and_errors):
    """extract_one with the download and unzip stubbed out and _sources returning a fixed answer."""
    from ugs_warehouse.pubs import harvest

    monkeypatch.setattr(harvest, "data_files", lambda sid: [])
    monkeypatch.setattr(harvest, "zip_urls", lambda sid: (None, "https://x/gis.zip"))
    monkeypatch.setattr(harvest, "download", lambda url, dst: open(dst, "wb").close())
    monkeypatch.setattr(vectors.zipfile, "ZipFile", lambda p: _NoopZip())
    monkeypatch.setattr(vectors, "_sources", lambda work, nested=(), used=None: srcs_and_errors)
    monkeypatch.setattr(vectors.gcs, "list_paths", lambda prefix: [])


class _NoopZip:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def infolist(self):
        return []

    def extractall(self, path, members=None):
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
    # Nothing extracted, so no manifest; the failure is on the attempt record instead, which the
    # next run uses to retry it after new pubs.
    assert set(store) == {f"{vectors.VECTORS_PREFIX}/M-106/_attempt.json"}
    attempt = json.loads(store[f"{vectors.VECTORS_PREFIX}/M-106/_attempt.json"])
    assert attempt["result"] == "fail:layers"
    assert attempt["errors"] == [{"source": "Broken.gdb", "error": "RuntimeError: x"}]


def test_a_failed_attempt_never_replaces_a_published_manifest(monkeypatch, tmp_path):
    """A retry that uploads nothing (a download timeout) must leave the manifest ingest publishes
    from alone; otherwise one transient failure would unpublish every layer of the pub."""
    from ugs_warehouse.pubs import harvest

    store = _mem_gcs(monkeypatch)
    good = f"{vectors.VECTORS_PREFIX}/M-112/_manifest.json"
    store[good] = b'{"spatial": ["faults"], "tables": [], "layers": [], "errors": [{"label": "x"}]}'
    monkeypatch.setattr(harvest, "data_files", lambda sid: [])
    monkeypatch.setattr(harvest, "zip_urls", lambda sid: (None, "https://x/gis.zip"))

    def _timeout(url, dst):
        raise TimeoutError("read timed out")

    monkeypatch.setattr(harvest, "download", _timeout)

    assert vectors.extract_one("M-112", force=True) == "fail:layers"
    assert json.loads(store[good])["spatial"] == ["faults"]


def test_a_bundle_of_only_unreadable_containers_is_unsupported_not_failed(monkeypatch, tmp_path):
    store = _mem_gcs(monkeypatch)
    err = {"source": "Map.mdb", "error": "unsupported format: personal geodatabase (.mdb), not extracted"}
    _stub_bundle(monkeypatch, tmp_path, ([], [err]))

    assert vectors.extract_one("M-113", force=True) == "unsupported"
    assert json.loads(store[f"{vectors.VECTORS_PREFIX}/M-113/_attempt.json"])["result"] == "unsupported"


def test_a_batch_run_lists_unsupported_pubs_without_re_downloading_them(monkeypatch, capsys):
    import sys

    from ugs_warehouse.pubs import harvest, source

    pfx = vectors.VECTORS_PREFIX
    monkeypatch.setattr(source, "read_pubs", lambda: [{"series_id": "SS-161"}, {"series_id": "M-2DM"}])
    _gis_zips(monkeypatch, lambda sid: (None, f"https://x/{sid}.zip"))
    monkeypatch.setattr(vectors.gcs, "list_paths", lambda prefix: [f"{pfx}/SS-161/_attempt.json"])
    monkeypatch.setattr(vectors.gcs, "get_bytes", lambda p: json.dumps(
        {"result": "unsupported", "extract_version": vectors.EXTRACT_VERSION,
         "errors": [{"source": "Ferron.mpk", "error": "unsupported format"}]}).encode())
    monkeypatch.setattr(vectors.gcs, "put_bytes", lambda b, p, **kw: None)
    downloads: list[str] = []
    monkeypatch.setattr(harvest, "download", lambda url, dst: downloads.append(url) or 1 / 0)
    monkeypatch.setattr(sys, "argv", ["vectors", "--all"])

    vectors.main()

    assert downloads == ["https://x/M-2DM.zip"]
    assert "SS-161 (Ferron.mpk)" in capsys.readouterr().out


def test_a_zip_with_nothing_to_extract_is_recorded_but_writes_no_manifest(monkeypatch, tmp_path):
    store = _mem_gcs(monkeypatch)
    _stub_bundle(monkeypatch, tmp_path, ([], []))

    assert vectors.extract_one("M-115", force=True) == "skip"
    # Ingest publishes from the manifest, so the finished-empty attempt lives on the attempt record.
    assert set(store) == {f"{vectors.VECTORS_PREFIX}/M-115/_attempt.json"}
    assert json.loads(store[f"{vectors.VECTORS_PREFIX}/M-115/_attempt.json"])["result"] == "skip"


def test_a_batch_run_lists_pubs_with_nothing_to_extract_without_re_downloading_them(
        monkeypatch, capsys):
    import sys

    from ugs_warehouse.pubs import harvest, source

    pfx = vectors.VECTORS_PREFIX
    monkeypatch.setattr(source, "read_pubs", lambda: [{"series_id": "M-1DM"}, {"series_id": "M-2DM"}])
    _gis_zips(monkeypatch, lambda sid: (None, f"https://x/{sid}.zip"))
    monkeypatch.setattr(vectors.gcs, "list_paths", lambda prefix: [f"{pfx}/M-1DM/_attempt.json"])
    monkeypatch.setattr(vectors.gcs, "get_bytes", lambda p: json.dumps(
        {"result": "skip", "extract_version": vectors.EXTRACT_VERSION}).encode())
    monkeypatch.setattr(vectors.gcs, "put_bytes", lambda b, p, **kw: None)
    downloads: list[str] = []
    monkeypatch.setattr(harvest, "download", lambda url, dst: downloads.append(url) or 1 / 0)
    monkeypatch.setattr(sys, "argv", ["vectors", "--all"])

    vectors.main()

    assert downloads == ["https://x/M-2DM.zip"]
    assert "not retried (--force to retry): M-1DM" in capsys.readouterr().out

    downloads.clear()
    monkeypatch.setattr(sys, "argv", ["vectors", "--all", "--force"])
    vectors.main()
    assert sorted(downloads) == ["https://x/M-1DM.zip", "https://x/M-2DM.zip"]


def test_a_batch_run_visits_only_new_pubs_with_a_gis_zip(monkeypatch):
    """--all lists the extracted series once and skips them, and never looks up a pub that has no
    GIS zip in its attachments, so a weekly run with nothing new downloads nothing."""
    import sys

    from ugs_warehouse.pubs import harvest, source, vectors

    monkeypatch.setattr(source, "read_pubs", lambda: [
        {"series_id": "M-1DM"}, {"series_id": "M-2DM"}, {"series_id": "M-3"}])
    gis = {"M-1DM": "https://x/m1-gis.zip", "M-2DM": "https://x/m2-gis.zip"}
    _gis_zips(monkeypatch, lambda sid: (None, gis.get(sid)))
    listings: list[str] = []

    def list_paths(prefix):
        listings.append(prefix)
        return [f"{vectors.VECTORS_PREFIX}/M-1DM/_manifest.json"]

    monkeypatch.setattr(vectors.gcs, "list_paths", list_paths)
    monkeypatch.setattr(vectors.gcs, "get_bytes",
                        lambda p: json.dumps({"spatial": ["a"], "layers": [], "errors": [],
                                              "extract_version": vectors.EXTRACT_VERSION}).encode())
    monkeypatch.setattr(vectors.gcs, "put_bytes", lambda b, p, **kw: None)
    downloads: list[str] = []
    monkeypatch.setattr(harvest, "download", lambda url, dst: downloads.append(url) or 1 / 0)
    monkeypatch.setattr(sys, "argv", ["vectors", "--all"])

    vectors.main()

    assert downloads == ["https://x/m2-gis.zip"]
    # Listings only, no per-pub lookups: the vectors prefix, and the units files the self-heal checks.
    assert set(listings) == {f"{vectors.VECTORS_PREFIX}/", f"{vectors.identity.UNITS_PREFIX}/"}


def test_a_batch_run_tries_new_pubs_before_retrying_failed_ones(monkeypatch):
    """A pub whose last run failed is retried after every pub never tried, so a slow failure
    cannot use up the job's timeout before new pubs are reached."""
    import sys

    from ugs_warehouse.pubs import harvest, source

    pfx = vectors.VECTORS_PREFIX
    monkeypatch.setattr(source, "read_pubs", lambda: [
        {"series_id": "M-1DM"}, {"series_id": "M-2DM"}, {"series_id": "M-3DM"}])
    _gis_zips(monkeypatch, lambda sid: (None, f"https://x/{sid}.zip"))
    monkeypatch.setattr(vectors.gcs, "list_paths", lambda prefix: [f"{pfx}/M-1DM/_manifest.json"])
    monkeypatch.setattr(vectors.gcs, "get_bytes",
                        lambda p: b'{"spatial": [], "layers": [], "errors": [{"source": "gis zip"}]}')
    monkeypatch.setattr(vectors.gcs, "put_bytes", lambda b, p, **kw: None)
    downloads: list[str] = []
    monkeypatch.setattr(harvest, "download", lambda url, dst: downloads.append(url) or 1 / 0)
    monkeypatch.setattr(sys, "argv", ["vectors", "--all"])

    vectors.main()

    assert downloads == ["https://x/M-2DM.zip", "https://x/M-3DM.zip", "https://x/M-1DM.zip"]


def test_a_failed_download_is_recorded_as_an_attempt(monkeypatch, tmp_path):
    from ugs_warehouse.pubs import harvest

    store = _mem_gcs(monkeypatch)
    monkeypatch.setattr(harvest, "data_files", lambda sid: [])
    monkeypatch.setattr(harvest, "zip_urls", lambda sid: (None, "https://x/gis.zip"))

    def _timeout(url, dst):
        raise TimeoutError("read timed out")

    monkeypatch.setattr(harvest, "download", _timeout)

    assert vectors.extract_one("M-111", force=True) == "fail:layers"
    doc = json.loads(store[f"{vectors.VECTORS_PREFIX}/M-111/_attempt.json"])
    assert doc["result"] == "fail:layers"
    assert doc["errors"] == [{"source": "GIS", "error": "TimeoutError: read timed out"}]
    assert f"{vectors.VECTORS_PREFIX}/M-111/_manifest.json" not in store


def test_a_bundle_over_the_unzip_limit_is_reported_not_extracted(monkeypatch, tmp_path):
    import zipfile

    bundle = tmp_path / "big.zip"
    with zipfile.ZipFile(bundle, "w") as z:
        z.writestr("faults.shp", b"x" * 2048)

    with pytest.raises(ValueError, match="limit for the job's in-memory disk"):
        vectors._extract_zip(str(bundle), str(tmp_path / "out"), remaining=1024)
    assert not (tmp_path / "out").exists() and bundle.exists()


def test_zips_inside_the_bundle_are_searched_too(tmp_path):
    import zipfile

    inner = tmp_path / "layers.zip"
    with zipfile.ZipFile(inner, "w") as z:
        z.writestr("faults.shp", b"")

    errors, dirs = vectors._unzip_nested(str(tmp_path), remaining=2**20)
    assert errors == [] and dirs == [str(tmp_path / "layers.zip_unzipped")]
    assert not inner.exists()  # deleted once extracted, so the in-memory disk holds one copy
    srcs, errors = vectors._sources(str(tmp_path), dirs)
    assert [s[2] for s in srcs] == ["faults"] and errors == []


def _zip(path, members):
    import zipfile

    with zipfile.ZipFile(path, "w") as z:
        for name, data in members.items():
            z.writestr(name, data)


def test_a_gdb_zip_extracts_to_a_single_geodatabase_folder(tmp_path):
    """`X.gdb.zip` holding bare geodatabase files becomes `X.gdb`; one holding an `X.gdb/` folder
    must not become `X.gdb/X.gdb`, which would read as a broken outer geodatabase."""
    _zip(tmp_path / "Bare.gdb.zip", {"a00000001.gdbtable": b""})
    _zip(tmp_path / "Wrapped.gdb.zip", {"Wrapped.gdb/a00000001.gdbtable": b""})

    errors, dirs = vectors._unzip_nested(str(tmp_path), remaining=2**20)

    assert errors == []
    assert (tmp_path / "Bare.gdb" / "a00000001.gdbtable").is_file()
    assert (tmp_path / "Wrapped.gdb.zip_unzipped" / "Wrapped.gdb" / "a00000001.gdbtable").is_file()
    assert not (tmp_path / "Wrapped.gdb").exists()


def test_an_inner_zip_never_merges_into_an_existing_folder(tmp_path):
    (tmp_path / "layers").mkdir()
    (tmp_path / "layers" / "faults.shp").write_bytes(b"outer")
    _zip(tmp_path / "layers.zip", {"faults.shp": b"inner"})

    vectors._unzip_nested(str(tmp_path), remaining=2**20)

    assert (tmp_path / "layers" / "faults.shp").read_bytes() == b"outer"


def test_a_failed_inner_zip_is_reported_and_leaves_nothing_behind(tmp_path):
    (tmp_path / "broken.zip").write_bytes(b"not a zip")

    errors, dirs = vectors._unzip_nested(str(tmp_path), remaining=2**20)

    assert [e["source"] for e in errors] == ["broken.zip"] and dirs == []
    assert list(tmp_path.iterdir()) == []


def test_an_inner_zip_over_the_budget_left_by_the_outer_one_is_reported(tmp_path):
    _zip(tmp_path / "big.zip", {"faults.shp": b"x" * 2048})

    errors, dirs = vectors._unzip_nested(str(tmp_path), remaining=1024)

    assert dirs == [] and "limit for the job's in-memory disk" in errors[0]["error"]


def test_zips_nested_deeper_than_the_limit_are_reported(tmp_path):
    level3 = tmp_path / "l3.zip"
    _zip(level3, {"faults.shp": b""})
    _zip(tmp_path / "l2.zip", {"l3.zip": level3.read_bytes()})
    level3.unlink()
    _zip(tmp_path / "l1.zip", {"l2.zip": (tmp_path / "l2.zip").read_bytes()})
    (tmp_path / "l2.zip").unlink()

    errors, _ = vectors._unzip_nested(str(tmp_path), remaining=2**20, depth=2)

    assert [e["source"] for e in errors] == ["l3.zip"]
    assert "nested more than 2 deep" in errors[0]["error"]


def test_readable_layers_beside_an_unsupported_container_are_extracted(monkeypatch, tmp_path):
    store = _mem_gcs(monkeypatch)
    table_df = pd.DataFrame({"MapUnit": ["Qal"]})
    monkeypatch.setattr("pyogrio.read_dataframe",
                         lambda path, layer=None, read_geometry=True: table_df)
    err = {"source": "Map.mdb", "error": "unsupported format: personal geodatabase (.mdb), not extracted"}
    _stub_bundle(monkeypatch, tmp_path, ([("b.gdb", "DMU", "geo__DMU", False)], [err]))

    assert vectors.extract_one("M-114", force=True) == "unsupported"
    manifest = json.loads(store[f"{vectors.VECTORS_PREFIX}/M-114/_manifest.json"])
    assert manifest["tables"] == ["geo__DMU"] and manifest["errors"] == [err]


def test_a_layer_from_an_inner_zip_never_takes_a_published_layers_name(tmp_path):
    """The outer bundle is labelled first, so `faults` stays the outer file's label even when an
    inner zip also ships a faults.shp that sorts earlier."""
    (tmp_path / "shapefiles").mkdir()
    (tmp_path / "shapefiles" / "faults.shp").write_bytes(b"")
    (tmp_path / "a_extra").mkdir()
    (tmp_path / "a_extra" / "faults.shp").write_bytes(b"")

    srcs, _ = vectors._sources(str(tmp_path), [str(tmp_path / "a_extra")])

    labels = {os.path.relpath(s[0], tmp_path): s[2] for s in srcs}
    assert labels == {"shapefiles/faults.shp": "faults", "a_extra/faults.shp": "a_extra__faults"}


def test_a_repeated_name_at_the_bundle_root_gets_a_suffix_not_the_temp_folder(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "faults.shp").write_bytes(b"")
    (tmp_path / "faults.SHP").write_bytes(b"")

    srcs, _ = vectors._sources(str(tmp_path))

    assert sorted(s[2] for s in srcs) == ["faults", "faults_2"]


def test_unsupported_containers_and_uppercase_extensions(tmp_path):
    (tmp_path / "MAP.SHP").write_bytes(b"")
    (tmp_path / "Wildcat24k.mdb").write_bytes(b"")
    (tmp_path / "Story.mpk").write_bytes(b"")

    srcs, errors = vectors._sources(str(tmp_path))

    assert [s[2] for s in srcs] == ["MAP"]
    assert [e["source"] for e in errors] == ["Story.mpk", "Wildcat24k.mdb"]
    assert all("unsupported format" in e["error"] for e in errors)


def test_same_named_shapefiles_in_different_folders_keep_distinct_labels(tmp_path):
    for folder in ("a", "b"):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / "faults.shp").write_bytes(b"")

    srcs, _ = vectors._sources(str(tmp_path))

    assert sorted(s[2] for s in srcs) == ["b__faults", "faults"]


# ---------------------------------------------------------------------------
# PMTiles: every extracted spatial layer is tiled beside its GeoParquet
# ---------------------------------------------------------------------------

def test_layer_tiling_reuses_the_serving_topic_tippecanoe_call(monkeypatch, tmp_path):
    """The pubs tiler goes through sink_pmtiles.run_tippecanoe: same binary and fixed flags, the
    label as the tile layer, no feature-id attribute (pub layers carry no feature_id), and the
    GeoJSONSeq it reads is in lon/lat."""
    import subprocess

    from ugs_warehouse.vector import sink_pmtiles

    real = sink_pmtiles.run_tippecanoe
    store = _mem_gcs(monkeypatch)
    monkeypatch.setattr(sink_pmtiles, "run_tippecanoe", real)
    seen: dict = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        with open(cmd[-1]) as fh:
            seen["feature"] = json.loads(fh.readline())
        with open(cmd[cmd.index("-o") + 1], "wb") as fh:
            fh.write(b"tiles")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(sink_pmtiles.shutil, "which", lambda b: f"/bin/{b}")
    monkeypatch.setattr(sink_pmtiles.subprocess, "run", fake_run)
    src, work = _geoparquet(tmp_path, {"Type": ["fault"]}, [Point(424000, 4512000)])
    entry = {"label": "geo__Faults", "proj_code": "EPSG:26912"}

    assert vectors._tile_layer(src, entry, "M-200", work) is True

    cmd = seen["cmd"]
    assert cmd[0] == sink_pmtiles.TIPPECANOE_BIN
    assert cmd[cmd.index("-l") + 1] == "geo__Faults"
    assert cmd[cmd.index("-l") + 2:-1] == [*sink_pmtiles.TILE_OPTS, *sink_pmtiles.EXTRA_OPTS]
    assert not any(c.startswith("--use-attribute-for-id") for c in cmd)
    lon, lat = seen["feature"]["geometry"]["coordinates"]
    assert -112 < lon < -111.8 and 40.7 < lat < 40.8  # lon first, whatever the CRS's axis order
    assert seen["feature"]["properties"] == {"Type": "fault"}  # the bbox column is not an attribute
    assert store == {vectors.pmtiles_object("M-200", "geo__Faults"): b"tiles"}
    assert os.listdir(work) == []  # temp files deleted: the job's disk is memory


def _geoparquet(tmp_path, props: dict, geoms: list, crs="EPSG:26912", index=None):
    """A GeoParquet written the way extraction writes one, in its own dir, and an empty work dir."""
    src, work = tmp_path / "src", tmp_path / "work"
    src.mkdir(exist_ok=True), work.mkdir(exist_ok=True)
    gdf = gpd.GeoDataFrame(props, geometry=geoms, crs=crs, index=index)
    vectors.geoparquet.write(gdf, src / "layer.parquet")
    return str(src / "layer.parquet"), str(work)


def _no_geopandas(monkeypatch):
    """Tiling must not hand the layer to geopandas: any of its read/reproject/write calls fails."""
    def boom(*a, **k):
        raise AssertionError("geopandas used while tiling")

    monkeypatch.setattr(gpd, "read_parquet", boom)
    monkeypatch.setattr(gpd, "read_file", boom)
    monkeypatch.setattr(gpd.GeoDataFrame, "to_crs", boom)
    monkeypatch.setattr(gpd.GeoDataFrame, "to_file", boom)


def _capture_geojsonl(monkeypatch) -> list[str]:
    """Stub tippecanoe, collecting the GeoJSONSeq lines it is handed."""
    lines: list[str] = []

    def tiler(geojsonl, pmtiles, layer, **kw):
        with open(geojsonl) as fh:
            lines.extend(fh)
        _fake_tippecanoe(geojsonl, pmtiles, layer)

    monkeypatch.setattr(vectors.sink_pmtiles, "run_tippecanoe", tiler)
    return lines


def test_tiling_makes_no_geopandas_call_and_puts_a_utm_point_at_the_right_lon_lat(monkeypatch, tmp_path):
    """The export is DuckDB's, from the manifest's WKT2 here (no EPSG code recorded). EPSG:26912
    northing/easting order must not swap the result."""
    from pyproj import CRS

    _mem_gcs(monkeypatch)
    lines = _capture_geojsonl(monkeypatch)
    src, work = _geoparquet(tmp_path, {"Type": ["fault"]}, [Point(424000, 4512000)])
    _no_geopandas(monkeypatch)
    entry = {"label": "wkt", "proj_code": None, "proj_wkt2": CRS.from_epsg(26912).to_wkt()}

    assert vectors._tile_layer(src, entry, "M-201", work) is True

    lon, lat = json.loads(lines[0])["geometry"]["coordinates"]
    assert (round(lon, 4), round(lat, 4)) == (-111.9003, 40.7554)


def test_a_layer_already_in_lon_lat_is_not_reprojected(monkeypatch, tmp_path):
    _mem_gcs(monkeypatch)
    lines = _capture_geojsonl(monkeypatch)
    src, work = _geoparquet(tmp_path, {"Type": ["a"]}, [Point(-111.9, 40.75)], crs="EPSG:4326")

    assert vectors._tile_layer(src, {"label": "ll", "proj_code": "EPSG:4326"}, "M-202", work)
    assert json.loads(lines[0])["geometry"]["coordinates"] == [-111.9, 40.75]


def test_tiling_falls_back_to_the_geoparquets_own_crs(monkeypatch, tmp_path):
    _mem_gcs(monkeypatch)
    lines = _capture_geojsonl(monkeypatch)
    src, work = _geoparquet(tmp_path, {"Type": ["a"]}, [Point(424000, 4512000)])

    assert vectors._tile_layer(src, {"label": "meta"}, "M-203", work) is True
    lon, lat = json.loads(lines[0])["geometry"]["coordinates"]
    assert -112 < lon < -111.8 and 40.7 < lat < 40.8


def test_tiling_a_layer_with_no_recorded_crs_raises_instead_of_guessing(monkeypatch, tmp_path):
    store = _mem_gcs(monkeypatch)
    src, work = _geoparquet(tmp_path, {"Type": ["a"]}, [Point(424000, 4512000)], crs=None)

    with pytest.raises(ValueError, match="no CRS recorded"):
        vectors._tile_layer(src, {"label": "nocrs"}, "M-204", work)
    assert store == {}


def test_extraction_releases_each_layer_before_reading_the_next(monkeypatch, tmp_path):
    import gc
    import weakref

    _mem_gcs(monkeypatch)
    alive: list = []

    def read_file(path, layer=None, engine=None):
        gc.collect()
        assert all(ref() is None for ref in alive), "the previous layer is still in memory"
        gdf = gpd.GeoDataFrame({"Type": ["fault"], "geometry": [Point(424000, 4512000)]},
                               crs="EPSG:26912")
        alive.append(weakref.ref(gdf))
        return gdf

    monkeypatch.setattr("geopandas.read_file", read_file)
    srcs = [("b.gdb", f"L{i}", f"b__L{i}", True) for i in range(3)]
    manifest = vectors._extract_and_upload(str(tmp_path), "M-210", srcs)
    assert manifest["errors"] == [] and len(alive) == 3


def test_a_layer_that_fails_is_released_before_the_next_is_read(monkeypatch, tmp_path):
    """The failure path `continue`s past the normal release; the next read still finds the failed
    layer's frame gone."""
    import gc
    import weakref

    _mem_gcs(monkeypatch)
    alive: list = []

    def read_file(path, layer=None, engine=None):
        gc.collect()
        assert all(ref() is None for ref in alive), "the failed layer is still in memory"
        gdf = gpd.GeoDataFrame({"Type": ["fault"], "geometry": [Point(424000, 4512000)]},
                               crs=None if layer == "L1" else "EPSG:26912")  # L1 fails: no CRS
        alive.append(weakref.ref(gdf))
        return gdf

    monkeypatch.setattr("geopandas.read_file", read_file)
    srcs = [("b.gdb", f"L{i}", f"b__L{i}", True) for i in range(3)]
    manifest = vectors._extract_and_upload(str(tmp_path), "M-211", srcs)
    assert [e["label"] for e in manifest["errors"]] == ["b__L1"] and len(alive) == 3


def test_rows_without_geometry_are_left_out_of_the_tiles(monkeypatch, tmp_path):
    from ugs_warehouse.vector import sink_pmtiles

    store = _mem_gcs(monkeypatch)
    lines: list[str] = []

    def tiler(geojsonl, pmtiles, layer, **kw):
        with open(geojsonl) as fh:
            lines.extend(fh)
        _fake_tippecanoe(geojsonl, pmtiles, layer)

    monkeypatch.setattr(sink_pmtiles, "run_tippecanoe", tiler)
    src, work = _geoparquet(tmp_path, {"Type": ["a", "b", "c"]},
                            [Point(424000, 4512000), None, Point()], index=[0, 0, 1])

    assert vectors._tile_layer(src, {"label": "pts", "proj_code": "EPSG:26912"}, "M-209", work) is True
    assert [json.loads(ln)["properties"]["Type"] for ln in lines] == ["a"]
    assert vectors.pmtiles_object("M-209", "pts") in store


def test_a_layer_with_no_geometry_writes_nothing(monkeypatch, tmp_path):
    store = _mem_gcs(monkeypatch)
    src, work = _geoparquet(tmp_path, {"Type": ["a", "b"]}, [None, Point()])

    assert vectors._tile_layer(src, {"label": "blank", "proj_code": "EPSG:26912"}, "M-212", work) is False
    assert store == {} and os.listdir(work) == []


def test_a_tippecanoe_failure_raises_with_its_stderr(monkeypatch):
    import subprocess

    from ugs_warehouse.vector import sink_pmtiles

    def boom(cmd, **kwargs):
        raise subprocess.CalledProcessError(1, cmd, stderr="bad geometry in feature 3")

    monkeypatch.setattr(sink_pmtiles.shutil, "which", lambda b: f"/bin/{b}")
    monkeypatch.setattr(sink_pmtiles.subprocess, "run", boom)

    with pytest.raises(RuntimeError, match="exited 1 for M-1/faults:\nbad geometry in feature 3"):
        sink_pmtiles.run_tippecanoe("in.geojsonl", "out.pmtiles", "faults", what="M-1/faults")


def test_a_missing_tippecanoe_binary_raises(monkeypatch):
    from ugs_warehouse.vector import sink_pmtiles

    monkeypatch.setattr(sink_pmtiles.shutil, "which", lambda b: None)
    with pytest.raises(RuntimeError, match="not on PATH"):
        sink_pmtiles.run_tippecanoe("in.geojsonl", "out.pmtiles", "faults")


def test_the_manifest_records_each_spatial_layers_pmtiles(monkeypatch, tmp_path):
    store = _mem_gcs(monkeypatch)
    spatial_gdf = gpd.GeoDataFrame({"Type": ["fault"], "geometry": [Point(424000, 4512000)]},
                                   crs="EPSG:26912")
    monkeypatch.setattr("geopandas.read_file", lambda path, layer=None, engine=None: spatial_gdf)
    monkeypatch.setattr("pyogrio.read_dataframe",
                        lambda path, layer=None, read_geometry=True: pd.DataFrame({"MapUnit": ["Qal"]}))

    vectors._extract_and_upload(str(tmp_path), "M-201", [
        ("b.gdb", "ContactsAndFaults", "geo__ContactsAndFaults", True),
        ("b.gdb", "DMU", "geo__DMU", False)])

    stored = json.loads(store[f"{vectors.VECTORS_PREFIX}/M-201/_manifest.json"])
    layers = {lay["label"]: lay for lay in stored["layers"]}
    assert layers["geo__ContactsAndFaults"]["pmtiles"] is True
    assert layers["geo__ContactsAndFaults"]["pmtiles_layer"] == "geo__ContactsAndFaults"
    assert "pmtiles" not in layers["geo__DMU"]  # a table is never tiled
    assert store[vectors.pmtiles_object("M-201", "geo__ContactsAndFaults")] == b"tiles:geo__ContactsAndFaults"
    assert stored["errors"] == []
    assert os.listdir(tmp_path) == []


def test_a_tiling_failure_is_a_warning_and_the_geoparquet_stays_published(monkeypatch, tmp_path):
    store = _mem_gcs(monkeypatch)
    spatial_gdf = gpd.GeoDataFrame({"Type": ["fault"], "geometry": [Point(424000, 4512000)]},
                                   crs="EPSG:26912")
    monkeypatch.setattr("geopandas.read_file", lambda path, layer=None, engine=None: spatial_gdf)

    def boom(geojsonl, pmtiles, layer, **kw):
        raise RuntimeError("tippecanoe exited 1 for M-202/faults")

    monkeypatch.setattr(vectors.sink_pmtiles, "run_tippecanoe", boom)

    manifest = vectors._extract_and_upload(str(tmp_path), "M-202", [("a.shp", None, "faults", True)])

    assert manifest["spatial"] == ["faults"]
    assert manifest["errors"] == []  # the series stays clean, so the batch run never re-downloads it
    assert manifest["warnings"] == [{
        "label": "faults", "code": vectors.PMTILES_FAILED,
        "warning": "PMTiles not built: RuntimeError: tippecanoe exited 1 for M-202/faults"}]
    assert vectors.tiling_failures(manifest) == ["faults"]
    assert "pmtiles" not in manifest["layers"][0]
    assert f"{vectors.VECTORS_PREFIX}/M-202/faults.parquet" in store
    assert vectors.pmtiles_object("M-202", "faults") not in store
    assert json.loads(store[f"{vectors.VECTORS_PREFIX}/M-202/_manifest.json"]) == manifest
    assert os.listdir(tmp_path) == []


def test_a_tiling_failure_keeps_the_pub_clean_and_is_listed_at_the_end_of_the_run(
        monkeypatch, tmp_path, capsys):
    import sys

    from ugs_warehouse.pubs import harvest

    store = _mem_gcs(monkeypatch)
    spatial_gdf = gpd.GeoDataFrame({"Type": ["fault"], "geometry": [Point(424000, 4512000)]},
                                   crs="EPSG:26912")
    monkeypatch.setattr("geopandas.read_file", lambda path, layer=None, engine=None: spatial_gdf)

    def boom(geojsonl, pmtiles, layer, **kw):
        raise RuntimeError("tippecanoe exited 1")

    monkeypatch.setattr(vectors.sink_pmtiles, "run_tippecanoe", boom)
    _stub_bundle(monkeypatch, tmp_path, ([("a.shp", None, "faults", True)], []))
    monkeypatch.setattr(vectors.gcs, "get_bytes", lambda p: store[p])

    untiled: list[str] = []
    assert vectors.extract_one("M-203", untiled=untiled) == "ok"
    assert untiled == ["M-203/faults"]
    assert vectors._read_manifest(f"{vectors.VECTORS_PREFIX}/M-203/_manifest.json")

    monkeypatch.setattr(harvest, "data_files", lambda sid: [])
    monkeypatch.setattr(harvest, "zip_urls", lambda sid: (None, "https://x/gis.zip"))
    monkeypatch.setattr(sys, "argv", ["vectors", "M-203", "M-204", "--force"])
    assert vectors.main() == 1
    err = capsys.readouterr().err
    assert ("2 layers published without PMTiles (tiling failed this run; the next run retries "
            "them): M-203/faults, M-204/faults") in err


# ---------------------------------------------------------------------------
# Self-heal: a run tiles layers and records relationships clean manifests are missing, from the
# GeoParquet already on GCS
# ---------------------------------------------------------------------------

def _backfill_store(monkeypatch, tmp_path, manifests: dict[str, dict]):
    """GCS double for the self-heal: each series' manifest plus a real GeoParquet per spatial layer,
    the versioned read/conditional write it uses, and a zip download that must not run."""
    from ugs_warehouse.pubs import harvest

    store = _mem_gcs(monkeypatch)
    gdf = gpd.GeoDataFrame({"Type": ["fault"], "geometry": [Point(424000, 4512000)]}, crs="EPSG:26912")
    for sid, doc in manifests.items():
        store[f"{vectors.VECTORS_PREFIX}/{sid}/_manifest.json"] = json.dumps(doc).encode()
        for lay in doc["layers"]:
            if lay.get("spatial"):
                local = tmp_path / f"{sid}_{lay['label']}.parquet"
                vectors.geoparquet.write(gdf, local)
                store[f"{vectors.VECTORS_PREFIX}/{sid}/{lay['label']}.parquet"] = local.read_bytes()

    def download(path, dest, **kw):
        with open(dest, "wb") as fh:
            fh.write(store[path])

    def put_if_unchanged(data, path, version, **kw):
        assert version == {"version": path}
        store[path] = data

    monkeypatch.setattr(vectors.gcs, "download", download)
    monkeypatch.setattr(vectors.gcs, "open_reader", lambda path: BytesIO(store[path]))
    monkeypatch.setattr(vectors.gcs, "get_bytes_versioned", lambda p: (store[p], {"version": p}))
    monkeypatch.setattr(vectors.gcs, "put_bytes_if_unchanged", put_if_unchanged)
    monkeypatch.setattr(vectors.gcs, "get_bytes", lambda p: store[p])
    monkeypatch.setattr(vectors.gcs, "list_paths", lambda prefix: [p for p in store if p.startswith(prefix)])
    monkeypatch.setattr(harvest, "download", lambda *a, **k: pytest.fail("self-heal re-downloaded a zip"))
    return store


def _manifest(*layers, errors=None, warnings=None, relations_version=vectors.relations.RELATIONS_VERSION):
    """A manifest whose relationships and unit product are current unless `relations_version`
    says otherwise."""
    return {"spatial": [lay["label"] for lay in layers if lay["spatial"]],
            "tables": [lay["label"] for lay in layers if not lay["spatial"]],
            "layers": list(layers), "empty": [], "errors": errors or [], "warnings": warnings or [],
            "extract_version": vectors.EXTRACT_VERSION,
            "unit_polys_version": vectors.unit_polys.UNIT_POLYS_VERSION,
            **({"relationships": [], "relations_version": relations_version}
               if relations_version is not None else {})}


def _heal(sids: list[str] | None, deadline: float | None = None) -> int:
    """Self-heal the given series (every clean one for None) as a run does: 1 when anything failed."""
    manifests = (vectors.clean_manifests(lambda sid, doc: vectors.needs_of(doc)) if sids is None
                 else {s: vectors.needs_of(m) for s in sids if (m := vectors._clean_manifest_of(s))})
    heal = vectors.self_heal(manifests, deadline=deadline)
    print(heal.summary())
    return 1 if heal.failed else 0


@pytest.mark.usefixtures("no_geopackage")
def test_self_heal_tiles_only_layers_without_pmtiles(monkeypatch, tmp_path, capsys):
    store = _backfill_store(monkeypatch, tmp_path, {"M-300": _manifest(
        {"label": "done", "spatial": True, "pmtiles": True, "pmtiles_layer": "done"},
        {"label": "todo", "spatial": True},
        {"label": "dmu", "spatial": False})})

    assert _heal(["M-300"]) == 0

    assert vectors.pmtiles_object("M-300", "todo") in store
    assert vectors.pmtiles_object("M-300", "done") not in store  # skipped, not rebuilt
    layers = {lay["label"]: lay for lay in
              json.loads(store[f"{vectors.VECTORS_PREFIX}/M-300/_manifest.json"])["layers"]}
    assert layers["todo"]["pmtiles"] is True and layers["todo"]["pmtiles_layer"] == "todo"
    assert "pmtiles" not in layers["dmu"]
    assert "Self-heal: 1 layers tiled, 0 pubs' relationships recorded" in capsys.readouterr().out


@pytest.mark.usefixtures("no_geopackage")
def test_self_heal_skips_a_layer_with_no_geometry_to_tile(monkeypatch, tmp_path, capsys):
    store = _backfill_store(monkeypatch, tmp_path, {"M-307": _manifest(
        {"label": "blank", "spatial": True},
        warnings=[{"label": "blank", "code": vectors.NO_GEOMETRY, "warning": "no geometry"}])})
    before = dict(store)

    assert _heal(["M-307"]) == 0
    assert store == before and "Self-heal: 0 layers tiled" in capsys.readouterr().out


@pytest.mark.usefixtures("no_geopackage")
def test_self_heal_visits_only_clean_manifests(monkeypatch, tmp_path):
    store = _backfill_store(monkeypatch, tmp_path, {
        "M-302": _manifest({"label": "a", "spatial": True}),
        "M-303": _manifest({"label": "b", "spatial": True},
                           errors=[{"label": "x", "error": "RuntimeError: unreadable"}])})

    assert _heal(None) == 0
    assert vectors.pmtiles_object("M-302", "a") in store
    assert vectors.pmtiles_object("M-303", "b") not in store  # not clean: extraction retries it


def test_a_layer_that_fails_to_tile_again_fails_the_heal_and_keeps_the_rest(monkeypatch, tmp_path, capsys):
    store = _backfill_store(monkeypatch, tmp_path, {"M-304": _manifest(
        {"label": "bad", "spatial": True}, {"label": "good", "spatial": True})})

    def tiler(geojsonl, pmtiles, layer, **kw):
        if layer == "bad":
            raise RuntimeError("tippecanoe exited 1 for M-304/bad")
        _fake_tippecanoe(geojsonl, pmtiles, layer)

    monkeypatch.setattr(vectors.sink_pmtiles, "run_tippecanoe", tiler)

    assert _heal(["M-304"]) == 1

    assert "1 layers tiled" in capsys.readouterr().out
    stored = json.loads(store[f"{vectors.VECTORS_PREFIX}/M-304/_manifest.json"])
    layers = {lay["label"]: lay for lay in stored["layers"]}
    assert layers["good"]["pmtiles"] is True and "pmtiles" not in layers["bad"]
    assert stored["errors"] == []  # the series stays clean, so extraction does not re-download it
    assert vectors.tiling_failures(stored) == ["bad"]  # the next run retries it


@pytest.mark.usefixtures("no_geopackage")
def test_self_heal_retries_a_failed_layer_and_clears_its_warning(monkeypatch, tmp_path):
    other = {"label": "a", "warning": "bbox not computed: x"}
    store = _backfill_store(monkeypatch, tmp_path, {"M-305": _manifest(
        {"label": "a", "spatial": True, "pmtiles": True, "pmtiles_layer": "a"},
        warnings=[{"label": "a", "code": vectors.PMTILES_FAILED, "warning": "PMTiles not built: x"},
                  other])})

    assert _heal(None) == 0  # a warning leaves the manifest clean, so it is visited

    assert vectors.pmtiles_object("M-305", "a") in store
    stored = json.loads(store[f"{vectors.VECTORS_PREFIX}/M-305/_manifest.json"])
    assert stored["warnings"] == [other]


def test_a_manifest_changed_during_the_heal_is_reported_not_overwritten(monkeypatch, tmp_path, capsys):
    store = _backfill_store(monkeypatch, tmp_path, {"M-308": _manifest({"label": "a", "spatial": True})})
    before = store[f"{vectors.VECTORS_PREFIX}/M-308/_manifest.json"]

    def changed(data, path, version, **kw):
        raise vectors.gcs.Changed(path)

    monkeypatch.setattr(vectors.gcs, "put_bytes_if_unchanged", changed)

    assert _heal(["M-308"]) == 1
    assert store[f"{vectors.VECTORS_PREFIX}/M-308/_manifest.json"] == before
    assert "M-308: self-heal FAIL Changed" in capsys.readouterr().err


@pytest.mark.parametrize("flag", ["--pmtiles-backfill", "--relations-backfill"])
def test_the_backfill_flags_are_gone(monkeypatch, flag):
    import sys

    monkeypatch.setattr(sys, "argv", ["vectors", flag])
    with pytest.raises(SystemExit):
        vectors.main()
    assert not hasattr(vectors, "backfill_pmtiles") and not hasattr(vectors, "backfill_relations")


def _batch_run(monkeypatch, sids: list[str]):
    """An `--all` run over `sids`, each listed with a GIS zip, nothing new to download."""
    import sys

    from ugs_warehouse.pubs import source

    monkeypatch.setattr(source, "read_pubs", lambda: [{"series_id": s} for s in sids])
    _gis_zips(monkeypatch, lambda sid: (None, "https://x/gis.zip"))
    monkeypatch.setattr(sys, "argv", ["vectors", "--all"])


def _gems_store(monkeypatch, tmp_path, sid: str, **manifest_kw):
    """A clean GeMS-shaped pub extracted before tiles and relationships were recorded."""
    store = _backfill_store(monkeypatch, tmp_path, {sid: _manifest(
        {"label": "geo__Polys", "spatial": True}, {"label": "geo__DMU", "spatial": False},
        relations_version=None, **manifest_kw)})
    polys = gpd.GeoDataFrame({"MapUnit": ["Qal", "Qaf"], "geometry": [Point(424000, 4512000)] * 2},
                             crs="EPSG:26912")
    local = tmp_path / f"{sid}_polys.parquet"
    vectors.geoparquet.write(polys, local)
    store[f"{vectors.VECTORS_PREFIX}/{sid}/geo__Polys.parquet"] = local.read_bytes()
    pd.DataFrame({"MapUnit": ["Qal", "Qaf", "Qc"]}).to_parquet(tmp_path / f"{sid}_dmu.parquet")
    store[f"{vectors.VECTORS_PREFIX}/{sid}/geo__DMU.parquet"] = (tmp_path / f"{sid}_dmu.parquet").read_bytes()
    return store


@pytest.mark.usefixtures("no_geopackage")
def test_a_batch_run_heals_a_clean_pub_missing_tiles_and_relationships(monkeypatch, tmp_path, capsys):
    store = _gems_store(monkeypatch, tmp_path, "M-1DM")
    _batch_run(monkeypatch, ["M-1DM"])

    assert vectors.main() == 0  # the clean pub is never re-downloaded (harvest.download fails)

    doc = json.loads(store[f"{vectors.VECTORS_PREFIX}/M-1DM/_manifest.json"])
    assert vectors.pmtiles_object("M-1DM", "geo__Polys") in store
    assert [(r["origin"], r["destination"]) for r in doc["relationships"]] == [("geo__Polys", "geo__DMU")]
    assert doc["relations_version"] == vectors.relations.RELATIONS_VERSION
    assert "Self-heal: 1 layers tiled, 1 pubs' relationships recorded" in capsys.readouterr().out
    capsys.readouterr()
    assert vectors.main() == 0  # nothing left: a second run does nothing
    assert "Self-heal: 0 layers tiled, 0 pubs' relationships recorded" in capsys.readouterr().out


@pytest.mark.usefixtures("no_geopackage")
def test_a_targeted_run_heals_the_pubs_it_names(monkeypatch, tmp_path):
    import sys

    store = _gems_store(monkeypatch, tmp_path, "M-2DM")
    monkeypatch.setattr(sys, "argv", ["vectors", "M-2DM"])
    assert vectors.main() == 0
    assert vectors.pmtiles_object("M-2DM", "geo__Polys") in store


@pytest.mark.usefixtures("no_geopackage")
def test_a_batch_run_keeps_what_each_manifest_needs_not_the_manifest(monkeypatch, tmp_path):
    store = _gems_store(monkeypatch, tmp_path, "M-3DM")
    path = f"{vectors.VECTORS_PREFIX}/M-3DM/_manifest.json"
    doc = json.loads(store[path])
    doc["layers"][0].update(columns=[{"name": "MapUnit", "type": "string"}], proj_wkt2="W" * 5000,
                            proj_code="EPSG:26912")
    store[path] = json.dumps(doc).encode()
    _batch_run(monkeypatch, ["M-3DM"])
    seen: dict = {}
    real = vectors.self_heal
    monkeypatch.setattr(vectors, "self_heal", lambda needs, **kw: seen.update(needs) or real(needs, **kw))

    assert vectors.main() == 0

    assert seen == {"M-3DM": vectors.Needs(("geo__Polys",), False, True)}
    assert "WWWW" not in repr(seen) and "MapUnit" not in repr(seen)


def test_the_tile_and_relations_heals_share_one_conditional_manifest_update(monkeypatch, tmp_path):
    store = _gems_store(monkeypatch, tmp_path, "M-4DM")
    updates: list[str] = []
    real = vectors._update_manifest
    monkeypatch.setattr(vectors, "_update_manifest",
                        lambda sid, change: updates.append(sid) or real(sid, change))

    built, errs = vectors._tile_missing("M-4DM")
    assert (built, errs) == (1, []) and vectors._relations_series("M-4DM") == []

    assert updates == ["M-4DM", "M-4DM"]
    doc = json.loads(store[f"{vectors.VECTORS_PREFIX}/M-4DM/_manifest.json"])
    assert doc["layers"][0]["pmtiles"] is True and doc["relations_version"]


@pytest.mark.usefixtures("no_geopackage")
def test_the_time_budget_stops_starting_series_and_leftovers_are_not_a_failure(monkeypatch, tmp_path, capsys):
    store = _backfill_store(monkeypatch, tmp_path, {
        f"M-{n}": _manifest({"label": "a", "spatial": True}, {"label": "b", "spatial": True})
        for n in (10, 11, 12)})
    _batch_run(monkeypatch, ["M-10", "M-11", "M-12"])
    real_tile = vectors._tile_missing
    healed: list[str] = []

    def tile(sid):
        healed.append(sid)
        return real_tile(sid)

    # The budget is spent once the first series has healed, however often the clock is read.
    monkeypatch.setattr(vectors, "_tile_missing", tile)
    monkeypatch.setattr(vectors.time, "monotonic", lambda: 9999.0 if healed else 0.0)

    assert vectors.main() == 0
    out = capsys.readouterr().out
    assert ("Self-heal: 2 layers tiled, 0 pubs' relationships recorded, units upgraded: 0, "
            "1 pubs' layer styles written, 0 GeoPackages built, 0 pubs' unit polygons built; 4 layers, "
            "0 pubs and 0 units files still pending, the next run continues") in out
    assert vectors.pmtiles_object("M-10", "a") in store  # the series in progress finished
    assert vectors.pmtiles_object("M-11", "a") not in store


def test_the_time_budget_stops_starting_extractions_and_lists_the_rest(monkeypatch, tmp_path, capsys):
    _backfill_store(monkeypatch, tmp_path, {})
    _batch_run(monkeypatch, ["M-30", "M-31", "M-32"])
    started: list[str] = []

    def extract(sid, **kw):
        started.append(sid)
        return "ok"

    # The budget is spent once the first pub has extracted, however often the clock is read.
    monkeypatch.setattr(vectors, "extract_one", extract)
    monkeypatch.setattr(vectors.time, "monotonic", lambda: 9999.0 if started else 0.0)

    assert vectors.main() == 0
    assert started == ["M-30"]
    assert ("Time budget used: 2 publications left to extract, the next run continues: M-31, M-32"
            in capsys.readouterr().out)


def test_a_targeted_run_extracts_every_pub_it_names_past_the_budget(monkeypatch, tmp_path):
    import sys

    _backfill_store(monkeypatch, tmp_path, {})
    started: list[str] = []

    def extract(sid, **kw):
        started.append(sid)
        return "ok"

    monkeypatch.setattr(vectors, "extract_one", extract)
    monkeypatch.setattr(vectors.time, "monotonic", lambda: 9999.0 if started else 0.0)
    monkeypatch.setattr(sys, "argv", ["vectors", "M-40", "M-41"])

    assert vectors.main() == 0
    assert started == ["M-40", "M-41"]


@pytest.mark.parametrize("step", ["_tile_missing", "_relations_series"])
@pytest.mark.usefixtures("no_geopackage")
def test_a_series_that_raises_fails_the_run_and_the_next_series_still_heals(
        monkeypatch, tmp_path, capsys, step):
    store = _gems_store(monkeypatch, tmp_path, "M-20")
    store.update({k.replace("/M-20/", "/M-21/"): v for k, v in list(store.items()) if "/M-20/" in k})
    _batch_run(monkeypatch, ["M-20", "M-21"])
    real = getattr(vectors, step)

    def flaky(sid):
        if sid == "M-20":
            raise ConnectionError("GCS unreachable")
        return real(sid)

    monkeypatch.setattr(vectors, step, flaky)
    assert vectors.main() == 1
    err = capsys.readouterr().err
    assert "M-20: self-heal FAIL ConnectionError: GCS unreachable" in err
    assert "1 self-heal failures this run" in err
    doc = json.loads(store[f"{vectors.VECTORS_PREFIX}/M-21/_manifest.json"])
    assert doc["relations_version"] == vectors.relations.RELATIONS_VERSION
    assert vectors.pmtiles_object("M-21", "geo__Polys") in store


@pytest.mark.usefixtures("no_geopackage")
def test_self_heal_records_a_raising_series_in_failed(monkeypatch, tmp_path):
    _backfill_store(monkeypatch, tmp_path, {"M-1": _manifest({"label": "a", "spatial": True}),
                                            "M-2": _manifest({"label": "b", "spatial": True})})
    real = vectors._tile_missing
    monkeypatch.setattr(vectors, "_tile_missing",
                        lambda sid: (_ for _ in ()).throw(OSError("boom")) if sid == "M-1" else real(sid))
    heal = vectors.self_heal(vectors.clean_manifests(lambda sid, doc: vectors.needs_of(doc)))
    assert heal.failed == ["M-1: OSError: boom"] and heal.tiled == 1


def test_a_targeted_run_does_not_heal_a_pub_it_just_extracted(monkeypatch, tmp_path, capsys):
    import sys

    store = _mem_gcs(monkeypatch)
    srcs = [("a.shp", None, "faults", True)]
    spatial_gdf = gpd.GeoDataFrame({"Type": ["fault"], "geometry": [Point(424000, 4512000)]},
                                   crs="EPSG:26912")
    monkeypatch.setattr("geopandas.read_file", lambda path, layer=None, engine=None: spatial_gdf)

    def boom(geojsonl, pmtiles, layer, **kw):
        raise RuntimeError("tippecanoe exited 1")

    monkeypatch.setattr(vectors.sink_pmtiles, "run_tippecanoe", boom)
    _stub_bundle(monkeypatch, tmp_path, (srcs, []))
    # The bucket as extraction leaves it, so the just-extracted pub's clean manifest is visible.
    monkeypatch.setattr(vectors.gcs, "list_paths", lambda prefix: [k for k in store if k.startswith(prefix)])
    monkeypatch.setattr(vectors.gcs, "get_bytes", lambda path: store[path])
    healed = []
    monkeypatch.setattr(vectors, "self_heal", lambda manifests, **k: healed.append(set(manifests))
                        or vectors.Heal())
    monkeypatch.setattr(sys, "argv", ["vectors", "M-205"])

    assert vectors.main() == 1  # the tiling failure, reported once, by extraction
    assert healed == [set()]
    assert capsys.readouterr().err.count("M-205/faults") == 1
    assert vectors._clean_manifest_of("M-205")  # clean, so only the exclusion kept it out


@pytest.mark.usefixtures("no_geopackage")
def test_a_retried_tiling_failure_fails_the_run(monkeypatch, tmp_path, capsys):
    _backfill_store(monkeypatch, tmp_path, {"M-1DM": _manifest(
        {"label": "failed", "spatial": True},
        warnings=[{"label": "failed", "code": vectors.PMTILES_FAILED, "warning": "PMTiles not built: x"}])})
    _batch_run(monkeypatch, ["M-1DM"])

    def boom(geojsonl, pmtiles, layer, **kw):
        raise RuntimeError("tippecanoe exited 1")

    monkeypatch.setattr(vectors.sink_pmtiles, "run_tippecanoe", boom)
    assert vectors.main() == 1
    assert "1 self-heal failures this run (the next run retries them): M-1DM/failed" in capsys.readouterr().err


@pytest.mark.usefixtures("no_geopackage")
def test_a_series_with_only_a_failed_layer_to_retry_goes_last(monkeypatch, tmp_path):
    _backfill_store(monkeypatch, tmp_path, {
        "M-1": _manifest({"label": "failed", "spatial": True}, warnings=[
            {"label": "failed", "code": vectors.PMTILES_FAILED, "warning": "x"}]),
        "M-2": _manifest({"label": "never", "spatial": True})})
    order = []
    monkeypatch.setattr(vectors, "_tile_missing", lambda sid: order.append(sid) or (1, []))
    vectors.self_heal(vectors.clean_manifests(lambda sid, doc: vectors.needs_of(doc)))
    assert order == ["M-2", "M-1"]


@pytest.mark.usefixtures("no_geopackage")
def test_a_relations_version_bump_recomputes_relationships(monkeypatch, tmp_path, capsys):
    store = _gems_store(monkeypatch, tmp_path, "M-3DM")
    assert _heal(["M-3DM"]) == 0
    capsys.readouterr()
    assert _heal(["M-3DM"]) == 0
    assert "0 pubs' relationships recorded" in capsys.readouterr().out

    monkeypatch.setattr(vectors.relations, "RELATIONS_VERSION", vectors.relations.RELATIONS_VERSION + 1)
    assert _heal(["M-3DM"]) == 0
    assert "1 pubs' relationships recorded" in capsys.readouterr().out
    doc = json.loads(store[f"{vectors.VECTORS_PREFIX}/M-3DM/_manifest.json"])
    assert doc["relations_version"] == vectors.relations.RELATIONS_VERSION


# ---------------------------------------------------------------------------
# Relationships: recorded at extraction, and by the self-heal from extracted GeoParquet
# ---------------------------------------------------------------------------

GDB = "OFR784DM_MillsJunction.gdb"


def _gems_frames(monkeypatch):
    """A GeMS-shaped bundle: map unit polygons, their description table and an unrelated glossary."""
    polys = gpd.GeoDataFrame({"MapUnit": ["Qal", "Qaf", "Qal"], "Notes": [None, None, None],
                              "geometry": [Point(424000, 4512000)] * 3}, crs="EPSG:26912")
    tables = {"DescriptionOfMapUnits": pd.DataFrame({"MapUnit": ["Qal", "Qaf", "Qc", None],
                                                     "Name": ["Alluvium", "Fan", "Colluvium", "Heading"]}),
              "Glossary": pd.DataFrame({"Term": ["certain", "questionable"]})}
    monkeypatch.setattr("geopandas.read_file", lambda path, layer=None, engine=None: polys)
    monkeypatch.setattr("pyogrio.read_dataframe",
                        lambda path, layer=None, read_geometry=True: tables[layer])
    stem = GDB[:-4]
    return [(GDB, "MapUnitPolys", f"{stem}__MapUnitPolys", True),
            (GDB, "DescriptionOfMapUnits", f"{stem}__DescriptionOfMapUnits", False),
            (GDB, "Glossary", f"{stem}__Glossary", False)]


def test_extraction_records_relationships_and_each_layers_kind_and_source(monkeypatch, tmp_path):
    _mem_gcs(monkeypatch)
    srcs = _gems_frames(monkeypatch)

    manifest = vectors._extract_and_upload(str(tmp_path), "OFR-784DM", srcs)

    stem = GDB[:-4]
    assert manifest["relationships"] == [{
        "origin": f"{stem}__MapUnitPolys", "origin_cols": ["MapUnit"],
        "destination": f"{stem}__DescriptionOfMapUnits", "destination_cols": ["MapUnit"],
        "cardinality": "many_to_one", "detected_by": "inferred", "confidence": 100, "overlap": 100.0}]
    assert {e["label"]: (e["kind"], e["source"]) for e in manifest["layers"]} == {
        f"{stem}__MapUnitPolys": ("layer", stem),
        f"{stem}__DescriptionOfMapUnits": ("table", stem),
        f"{stem}__Glossary": ("supporting", stem)}
    assert manifest["warnings"] == []


def test_unreadable_declared_relationships_warn_once_per_geodatabase_and_inference_still_runs(
        monkeypatch, tmp_path):
    _mem_gcs(monkeypatch)
    srcs = _gems_frames(monkeypatch)
    monkeypatch.setattr(vectors.relations, "read_declared", lambda gdb: (None, "ogrinfo is not on PATH"))

    manifest = vectors._extract_and_upload(str(tmp_path), "OFR-784DM", srcs)

    assert manifest["warnings"] == [{"source": GDB, "code": vectors.relations.RELATIONSHIPS_NOT_READ,
                                     "warning": "declared relationships not read: ogrinfo is not on PATH"}]
    assert [r["detected_by"] for r in manifest["relationships"]] == ["inferred"]
    assert manifest["errors"] == []  # a warning, not an error: the pub stays extracted


def test_declared_relationships_replace_inferred_ones_and_unpublishable_ones_warn(monkeypatch, tmp_path):
    _mem_gcs(monkeypatch)
    srcs = _gems_frames(monkeypatch)
    declared = {"name": "dmu_rel", "cardinality": "OneToMany",
                "left_table_name": "DescriptionOfMapUnits", "right_table_name": "MapUnitPolys",
                "left_table_fields": ["MapUnit"], "right_table_fields": ["MapUnit"]}
    stations = {**declared, "name": "stations_rel", "left_table_name": "Stations"}
    monkeypatch.setattr(vectors.relations, "read_declared", lambda gdb: ([declared, stations], None))

    manifest = vectors._extract_and_upload(str(tmp_path), "OFR-784DM", srcs)

    stem = GDB[:-4]
    assert manifest["relationships"] == [{
        "origin": f"{stem}__MapUnitPolys", "origin_cols": ["MapUnit"],
        "destination": f"{stem}__DescriptionOfMapUnits", "destination_cols": ["MapUnit"],
        "cardinality": "many_to_one", "detected_by": "declared"}]
    [warning] = manifest["warnings"]
    assert warning["code"] == vectors.relations.RELATIONSHIP_SKIPPED and "stations_rel" in warning["warning"]


def test_a_layer_that_cannot_be_profiled_is_an_error_and_extraction_goes_on(monkeypatch, tmp_path):
    store = _mem_gcs(monkeypatch)
    srcs = _gems_frames(monkeypatch)
    real = vectors.relations.profile_frame

    def boom(frame, keys=True):
        if "Term" in frame.columns:
            raise MemoryError("glossary too big")
        return real(frame, keys)

    monkeypatch.setattr(vectors.relations, "profile_frame", boom)
    manifest = vectors._extract_and_upload(str(tmp_path), "OFR-784DM", srcs)

    assert manifest["errors"] == [{"label": srcs[2][2],
                                   "error": "not profiled for relationships: MemoryError: glossary too big"}]
    assert len(manifest["layers"]) == 3 and f"{vectors.VECTORS_PREFIX}/OFR-784DM/_manifest.json" in store
    assert [r["destination_cols"] for r in manifest["relationships"]] == [["MapUnit"]]


def test_a_column_that_cannot_be_profiled_warns_once_and_the_rest_still_join(monkeypatch, tmp_path):
    store = _mem_gcs(monkeypatch)
    srcs = _gems_frames(monkeypatch)
    real = vectors.relations.profile_column

    def flaky(arr, keys=True):
        if "Alluvium" in arr.to_pylist() or "certain" in arr.to_pylist():
            raise TypeError("unhashable")
        return real(arr, keys)

    monkeypatch.setattr(vectors.relations, "profile_column", flaky)
    manifest = vectors._extract_and_upload(str(tmp_path), "OFR-784DM", srcs)

    assert len(manifest["layers"]) == 3 and manifest["errors"] == []
    assert f"{vectors.VECTORS_PREFIX}/OFR-784DM/{srcs[0][2]}.parquet" in store
    assert [(w["code"], w["label"].split("__")[1]) for w in manifest["warnings"]] == [
        (vectors.relations.PROFILE_FAILED, "DescriptionOfMapUnits"),
        (vectors.relations.PROFILE_FAILED, "Glossary")]
    assert "Name: TypeError: unhashable" in manifest["warnings"][0]["warning"]
    assert [r["destination_cols"] for r in manifest["relationships"]] == [["MapUnit"]]


def test_a_shapefiles_source_is_its_folder_in_the_bundle(tmp_path):
    work = str(tmp_path)
    def name(path, layer, label):
        return vectors._dataset_name(work, "M-246DM", os.path.join(work, *path), layer, label)

    assert name(["faults.shp"], None, "faults") == "M-246DM"
    assert name(["Landslides", "a.shp"], None, "a") == "Landslides"
    assert name(["Landslides", "a.shp"], None, "Landslides__a") == "Landslides"  # collided: same scope
    # A geodatabase's scope is its label's prefix, which names the folder when its name collided,
    # so the backfill reads the same scope from the label alone.
    assert name(["x", "Geo.gdb"], "Faults", "Geo__Faults") == "Geo"
    assert name(["x", "Geo.gdb"], "Faults", "x__Geo__Faults") == "x__Geo"


def _relations_store(monkeypatch, tmp_path, sid: str, frames: dict, **manifest_extra):
    """GCS double for the relationships backfill: a manifest from before relationships were
    recorded, and each layer's (Geo)Parquet."""
    store = _backfill_store(monkeypatch, tmp_path, {})
    layers = []
    for label, frame in frames.items():
        local = tmp_path / f"{label}.parquet"
        spatial = isinstance(frame, gpd.GeoDataFrame)
        if spatial:
            vectors.geoparquet.write(frame, local)
        else:
            frame.to_parquet(local)
        store[f"{vectors.VECTORS_PREFIX}/{sid}/{label}.parquet"] = local.read_bytes()
        layers.append({"label": label, "spatial": spatial, "rows": len(frame),
                       **({"pmtiles": True, "pmtiles_layer": label} if spatial else {})})
    doc = {**_manifest(*layers, relations_version=None), **manifest_extra}
    store[f"{vectors.VECTORS_PREFIX}/{sid}/_manifest.json"] = json.dumps(doc).encode()
    return store


def _gems_parquet():
    return {"geo__MapUnitPolys": gpd.GeoDataFrame(
                {"MapUnit": ["Qal", "Qaf", "Qal"], "Shape_Area": [1.0, 2.0, 3.0],
                 "geometry": [Point(424000, 4512000)] * 3}, crs="EPSG:26912"),
            "geo__DescriptionOfMapUnits": pd.DataFrame({"MapUnit": ["Qal", "Qaf", "Qc"],
                                                        "Age": [1.5, 2.5, 3.5]}),
            "geo__Glossary": pd.DataFrame({"Term": ["certain", "questionable"]})}


@pytest.mark.usefixtures("no_geopackage")
def test_relations_backfill_records_them_from_the_extracted_geoparquet(monkeypatch, tmp_path, capsys):
    import pyarrow.parquet as pq

    store = _relations_store(monkeypatch, tmp_path, "M-400", _gems_parquet())
    read = []
    real = pq.ParquetFile.read
    monkeypatch.setattr(pq.ParquetFile, "read", lambda self, columns=None, **kw: read.append(columns)
                        or real(self, columns=columns, **kw))
    # The key columns are range-read from the object; the file is never downloaded whole.
    monkeypatch.setattr(vectors.gcs, "download", lambda *a, **k: pytest.fail("downloaded the file"))

    assert _heal(["M-400"]) == 0

    doc = json.loads(store[f"{vectors.VECTORS_PREFIX}/M-400/_manifest.json"])
    assert [(r["origin"], r["destination"], r["detected_by"]) for r in doc["relationships"]] == [
        ("geo__MapUnitPolys", "geo__DescriptionOfMapUnits", "inferred")]
    assert {e["label"]: (e["kind"], e["source"]) for e in doc["layers"]} == {
        "geo__MapUnitPolys": ("layer", "geo"), "geo__DescriptionOfMapUnits": ("table", "geo"),
        "geo__Glossary": ("supporting", "geo")}
    assert [w["code"] for w in doc["warnings"]] == [vectors.relations.RELATIONSHIPS_NOT_READ]
    # Only string and integer columns are read: no geometry, no floats.
    assert read == [["MapUnit"], ["MapUnit"], ["Term"]]
    assert "1 pubs' relationships recorded" in capsys.readouterr().out


@pytest.mark.usefixtures("no_geopackage")
def test_relations_backfill_keeps_declared_relationships_and_is_idempotent(monkeypatch, tmp_path):
    declared = {"origin": "geo__Glossary", "origin_cols": ["Term"],
                "destination": "geo__DescriptionOfMapUnits", "destination_cols": ["MapUnit"],
                "cardinality": "many_to_one", "detected_by": "declared"}
    store = _relations_store(monkeypatch, tmp_path, "M-401", _gems_parquet(), relationships=[declared])

    assert _heal(["M-401"]) == 0
    doc = json.loads(store[f"{vectors.VECTORS_PREFIX}/M-401/_manifest.json"])
    assert doc["relationships"][0] == declared and len(doc["relationships"]) == 2
    assert doc["warnings"] == []  # the extraction that recorded them already reported what it read

    before = store[f"{vectors.VECTORS_PREFIX}/M-401/_manifest.json"]
    monkeypatch.setattr(vectors.gcs, "put_bytes_if_unchanged",
                        lambda *a, **k: pytest.fail("an unchanged manifest was rewritten"))
    assert _heal(["M-401"]) == 0
    assert store[f"{vectors.VECTORS_PREFIX}/M-401/_manifest.json"] == before


def test_extraction_and_backfill_scope_collided_geodatabases_the_same_way(monkeypatch, tmp_path):
    _mem_gcs(monkeypatch)
    srcs = _gems_frames(monkeypatch)
    # A second copy of the geodatabase in a subfolder: its labels take the folder as a prefix.
    stem = GDB[:-4]
    srcs += [(f"sub/{GDB}", layer, f"sub__{label}", spatial) for _, layer, label, spatial in srcs]
    manifest = vectors._extract_and_upload(str(tmp_path), "OFR-784DM", srcs)

    for entry in manifest["layers"]:
        assert entry["source"] == vectors.relations.source_of_label(entry["label"], "OFR-784DM")
    # Each copy joins within itself only.
    assert sorted((r["origin"], r["destination"]) for r in manifest["relationships"]) == [
        (f"{stem}__MapUnitPolys", f"{stem}__DescriptionOfMapUnits"),
        (f"sub__{stem}__MapUnitPolys", f"sub__{stem}__DescriptionOfMapUnits")]


@pytest.mark.usefixtures("no_geopackage")
def test_relations_backfill_leaves_shapefiles_of_unknown_folder_out(monkeypatch, tmp_path):
    polys = gpd.GeoDataFrame({"MapUnit": ["Qal", "Qaf"], "geometry": [Point(424000, 4512000)] * 2},
                             crs="EPSG:26912")
    units = pd.DataFrame({"MapUnit": ["Qal", "Qaf", "Qc"]})
    # `sub__faults` shows `faults` was taken, so the bundle has several folders and the bare labels'
    # folders are unknown: `faults` and `units` may not sit together.
    store = _relations_store(monkeypatch, tmp_path, "M-403",
                             {"faults": polys, "sub__faults": polys, "units": units})
    del store[f"{vectors.VECTORS_PREFIX}/M-403/faults.parquet"]  # never read
    del store[f"{vectors.VECTORS_PREFIX}/M-403/units.parquet"]

    assert _heal(["M-403"]) == 0
    doc = json.loads(store[f"{vectors.VECTORS_PREFIX}/M-403/_manifest.json"])
    assert doc["relationships"] == []
    scope = [w for w in doc["warnings"] if w["code"] == vectors.relations.SCOPE_UNKNOWN]
    assert len(scope) == 1 and "faults, units" in scope[0]["warning"] and "--force" in scope[0]["warning"]
    assert {e["label"]: e.get("source") for e in doc["layers"]} == {
        "faults": None, "sub__faults": "sub", "units": None}

    before = store[f"{vectors.VECTORS_PREFIX}/M-403/_manifest.json"]
    assert _heal(["M-403"]) == 0  # the same warning, not a second one
    assert store[f"{vectors.VECTORS_PREFIX}/M-403/_manifest.json"] == before


@pytest.mark.usefixtures("no_geopackage")
def test_relations_backfill_scopes_bare_shapefiles_together_when_there_is_one_folder(monkeypatch, tmp_path):
    polys = gpd.GeoDataFrame({"MapUnit": ["Qal", "Qaf"], "geometry": [Point(424000, 4512000)] * 2},
                             crs="EPSG:26912")
    store = _relations_store(monkeypatch, tmp_path, "M-404",
                             {"faults": polys, "units": pd.DataFrame({"MapUnit": ["Qal", "Qaf", "Qc"]})})
    assert _heal(["M-404"]) == 0
    doc = json.loads(store[f"{vectors.VECTORS_PREFIX}/M-404/_manifest.json"])
    assert [(r["origin"], r["destination"]) for r in doc["relationships"]] == [("faults", "units")]
    assert {e["source"] for e in doc["layers"]} == {"M-404"}


def test_relations_backfill_records_nothing_when_a_layer_cannot_be_read(monkeypatch, tmp_path, capsys):
    store = _relations_store(monkeypatch, tmp_path, "M-402", _gems_parquet())
    del store[f"{vectors.VECTORS_PREFIX}/M-402/geo__Glossary.parquet"]
    before = _stored_manifest(store, "M-402")

    assert _heal(["M-402"]) == 1
    after = _stored_manifest(store, "M-402")
    for key in ("relationships", "relations_version"):  # styles are written; relationships are not
        assert after.get(key) == before.get(key)
    assert [lay.get("kind") for lay in after["layers"]] == [lay.get("kind") for lay in before["layers"]]
    assert "M-402/geo__Glossary: not read for relationships" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# The pubs ingest runs the same self-heal before it writes the items
# ---------------------------------------------------------------------------

def _ingest(monkeypatch, store, pubs: list[dict], *, file_meta: bool = False):
    """A pubs ingest over `store`: the vector manifests and GeoParquet are real, the other
    publication inputs empty, and the written Collections and Items captured. With `file_meta`,
    the real `_file_meta_index` runs, its listing served from `store` (real sizes and sha256
    multihashes), since the in-memory bucket has no google-cloud listing of its own."""
    import hashlib

    from ugs_warehouse.core import stac
    from ugs_warehouse.pubs import editions, ingest, source

    written: list[dict] = []
    monkeypatch.setattr(source, "read_pubs", lambda: pubs)
    monkeypatch.setattr(source, "read_attachments", lambda: [])
    monkeypatch.setattr(source, "source_name", lambda: "test")
    monkeypatch.setattr(editions, "footprint_rows", lambda: [])
    if file_meta:
        monkeypatch.setattr(vectors.gcs, "list_file_meta", lambda prefix: {
            p: vectors.gcs.FileMeta(len(b), "1220" + hashlib.sha256(b).hexdigest())
            for p, b in store.items() if p.startswith(prefix)})
    else:
        monkeypatch.setattr(ingest, "_file_meta_index", lambda: {})
    monkeypatch.setattr(ingest, "_cog_headers", lambda cogs: {})
    monkeypatch.setattr(ingest, "_build_search_corpus", lambda: None)
    monkeypatch.setattr(ingest, "_published_items", lambda pubs: {})
    monkeypatch.setattr(stac, "prior_collection_field", lambda *a, **k: None)
    monkeypatch.setattr(stac, "prior_property", lambda *a, **k: None)
    monkeypatch.setattr(stac, "attach_renders", lambda item: None)
    monkeypatch.setattr(stac.styles, "warm", lambda: 0)
    monkeypatch.setattr(stac, "write_item", written.append)
    monkeypatch.setattr(stac, "write_collection", lambda c, children: written.append(c))
    return ingest, written


def _two_gems_pubs(monkeypatch, tmp_path, first: str, second: str):
    store = _gems_store(monkeypatch, tmp_path, first)
    store.update({k.replace(f"/{first}/", f"/{second}/"): v
                  for k, v in list(store.items()) if f"/{first}/" in k})
    return store


def _no_tiling(monkeypatch):
    """Fail the test if anything tiles: the ingest job (4Gi) must leave tiling to the vectors job."""
    def boom(*a, **k):
        raise AssertionError("ingest tiled a layer")

    monkeypatch.setattr(vectors.sink_pmtiles, "run_tippecanoe", boom)
    monkeypatch.setattr(vectors, "_tile_missing", boom)
    monkeypatch.setattr(vectors, "_tile_layer", boom)


@pytest.mark.usefixtures("no_geopackage")
def test_an_ingest_heals_relationships_but_never_tiles(monkeypatch, tmp_path, capsys):
    store = _gems_store(monkeypatch, tmp_path, "M-1DM")
    ingest, written = _ingest(monkeypatch, store, [{"series_id": "M-1DM", "pub_year": "2020"}])
    _no_tiling(monkeypatch)

    assert ingest.build_catalog(skip_refresh=True) == 1

    assert vectors.pmtiles_object("M-1DM", "geo__Polys") not in store     # the vectors job tiles
    polys = next(d for d in written if d["id"] == "M-1DM__geo__Polys")
    assert "pmtiles" not in polys["assets"]
    assert polys["assets"]["geo__DMU"]["roles"] == ["data", "related"]    # related in this run
    out = capsys.readouterr().out
    assert "[pubs] Self-heal: 1 pubs' relationships recorded, units upgraded: 0" in out
    assert "tiled" not in out


def test_ingest_leaves_work_past_its_budget_for_the_next_refresh_without_failing(monkeypatch, tmp_path, capsys):
    store = _gems_store(monkeypatch, tmp_path, "M-1DM")
    ingest, written = _ingest(monkeypatch, store, [{"series_id": "M-1DM", "pub_year": "2020"}])
    monkeypatch.setattr(ingest, "HEAL_BUDGET_S", 0)

    assert ingest.build_catalog(skip_refresh=True) == 1  # no raise
    assert ("1 pubs and 0 units files still pending, the next refresh continues"
            in capsys.readouterr().out)
    assert any(d["id"] == "M-1DM__geo__Polys" for d in written)  # written from the manifest as is


def test_a_heal_failure_fails_the_ingest_like_a_publication(monkeypatch, tmp_path, capsys):
    store = _gems_store(monkeypatch, tmp_path, "M-1DM")
    ingest, written = _ingest(monkeypatch, store, [{"series_id": "M-1DM", "pub_year": "2020"}])
    del store[f"{vectors.VECTORS_PREFIX}/M-1DM/geo__DMU.parquet"]  # its keys cannot be read

    with pytest.raises(RuntimeError, match=r"1 publication\(s\) failed: \['M-1DM'\]"):
        ingest.build_catalog(skip_refresh=True)
    err = capsys.readouterr().err
    assert "[pubs] FAILED M-1DM: self-heal: 1 failed this run: M-1DM/geo__DMU: " in err
    assert any(d["id"] == "M-1DM__geo__Polys" for d in written)  # the pub is still written


@pytest.mark.usefixtures("no_geopackage")
def test_an_ingest_limited_to_a_series_heals_only_that_series(monkeypatch, tmp_path):
    store = _two_gems_pubs(monkeypatch, tmp_path, "M-1DM", "OFR-5")
    ingest, _ = _ingest(monkeypatch, store, [{"series_id": "M-1DM", "pub_year": "2020"},
                                             {"series_id": "OFR-5", "pub_year": "2020"}])

    assert ingest.build_catalog(series="M", skip_refresh=True) == 1
    assert json.loads(store[f"{vectors.VECTORS_PREFIX}/M-1DM/_manifest.json"])["relationships"]
    assert "relationships" not in json.loads(store[f"{vectors.VECTORS_PREFIX}/OFR-5/_manifest.json"])


def test_a_pub_whose_heal_and_write_both_fail_counts_once(monkeypatch, tmp_path, capsys):
    from ugs_warehouse.pubs import sink_stac

    store = _gems_store(monkeypatch, tmp_path, "M-1DM")
    ingest, _ = _ingest(monkeypatch, store, [{"series_id": "M-1DM", "pub_year": "2020"}])
    del store[f"{vectors.VECTORS_PREFIX}/M-1DM/geo__DMU.parquet"]

    def unwritable(*a, **k):
        raise ValueError("bad publication")

    monkeypatch.setattr(sink_stac, "build_publication", unwritable)
    with pytest.raises(RuntimeError, match=r"^1 publication\(s\) failed: \['M-1DM'\]$"):
        ingest.build_catalog(skip_refresh=True)
    err = capsys.readouterr().err
    assert "FAILED M-1DM: self-heal" in err and "FAILED M-1DM: ValueError: bad publication" in err


@pytest.mark.usefixtures("no_geopackage")
def test_the_ingest_reads_each_manifest_once_and_rereads_only_what_the_heal_changed(monkeypatch, tmp_path):
    store = _two_gems_pubs(monkeypatch, tmp_path, "M-1DM", "OFR-5")
    # OFR-5 is already current; only M-1DM needs relationships.
    ofr = f"{vectors.VECTORS_PREFIX}/OFR-5/_manifest.json"
    doc = json.loads(store[ofr])
    doc.update(relationships=[], relations_version=vectors.relations.RELATIONS_VERSION)
    doc["layers"][0].update(pmtiles=True, pmtiles_layer="geo__Polys")
    store[ofr] = json.dumps(doc).encode()
    ingest, _ = _ingest(monkeypatch, store, [{"series_id": "M-1DM", "pub_year": "2020"},
                                             {"series_id": "OFR-5", "pub_year": "2020"}])
    reads: list[str] = []
    real = vectors.gcs.get_bytes

    def counting(path, **kw):
        if path.endswith("_manifest.json"):
            reads.append(path.split("/")[-2])
        return real(path, **kw)

    monkeypatch.setattr(vectors.gcs, "get_bytes", counting)
    monkeypatch.setattr(ingest.gcs, "get_bytes", counting)

    assert ingest.build_catalog(skip_refresh=True) == 2

    assert sorted(reads) == ["M-1DM", "M-1DM", "OFR-5"]  # the healed one twice, the rest once


@pytest.mark.usefixtures("no_geopackage")
def test_the_needs_index_holds_labels_not_manifest_content(monkeypatch, tmp_path):
    """The ingest keeps a small index for the heal; the columns and WKT2 live only in the reduced
    manifests the items are built from."""
    store = _gems_store(monkeypatch, tmp_path, "M-1DM")
    doc = json.loads(store[f"{vectors.VECTORS_PREFIX}/M-1DM/_manifest.json"])
    doc["layers"][0].update(columns=[{"name": "MapUnit", "type": "string"}], proj_wkt2="W" * 5000)
    store[f"{vectors.VECTORS_PREFIX}/M-1DM/_manifest.json"] = json.dumps(doc).encode()
    from ugs_warehouse.pubs import ingest

    _, _, needs = ingest._read_vector_manifests()

    assert needs == {"M-1DM": vectors.Needs(("geo__Polys",), False, True)}
    assert "WWWW" not in repr(needs) and "MapUnit" not in repr(needs)


def test_a_clean_manifests_reducer_runs_in_the_reading_thread_and_keeps_only_its_result(monkeypatch, tmp_path):
    store = _backfill_store(monkeypatch, tmp_path, {"M-6": _manifest({"label": "a", "spatial": True})})
    kept = vectors.clean_manifests(lambda sid, doc: (sid, len(doc["layers"])))
    assert kept == {"M-6": ("M-6", 1)} and store


@pytest.mark.usefixtures("no_geopackage")
def test_healed_relationships_reach_the_items_in_the_same_run(monkeypatch, tmp_path):
    store = _gems_store(monkeypatch, tmp_path, "M-1DM")
    ingest, written = _ingest(monkeypatch, store, [{"series_id": "M-1DM", "pub_year": "2020"}],
                              file_meta=True)

    assert ingest.build_catalog(skip_refresh=True) == 1
    dmu = next(d for d in written if d["id"] == "M-1DM__geo__DMU")
    assert dmu["assets"]["data"]["file:size"] == len(
        store[f"{vectors.VECTORS_PREFIX}/M-1DM/geo__DMU.parquet"])


# ---------------------------------------------------------------------------
# Units files older than GeoParquet 1.1 are rewritten by the same self-heal
# ---------------------------------------------------------------------------

def _units_store(monkeypatch, tmp_path, files: dict[str, str]):
    """An in-memory bucket of units files, {series id: "1.0" | "1.1"}, written by geopandas
    (1.0, no covering) or the pubs writer (1.1 with a bbox covering). Records upload headers and
    serves footers by range."""
    store = _backfill_store(monkeypatch, tmp_path, {})
    uploads: dict[str, str] = {}
    real_upload = vectors.gcs.upload

    def upload(local, path, **kw):
        uploads[path] = kw.get("cache_control")
        return real_upload(local, path, **kw)

    monkeypatch.setattr(vectors.gcs, "upload", upload)
    monkeypatch.setattr(vectors.gcs, "get_tail", lambda p, length: store[p][-length:])
    units = gpd.GeoDataFrame({"MapUnit": ["Qal", "Qaf"]},
                             geometry=[Point(-112.0, 40.0), Point(-112.1, 40.1)], crs="EPSG:4326")
    for sid, version in files.items():
        local = tmp_path / f"{sid}.units.parquet"
        if version == "1.0":
            units.to_parquet(local, schema_version="1.0.0")
        else:
            vectors.geoparquet.write(units, local)
        store[f"{vectors.identity.UNITS_PREFIX}/{sid}/{sid}.units.parquet"] = local.read_bytes()
    return store, uploads


def _geo(raw: bytes) -> dict:
    import pyarrow as pa
    import pyarrow.parquet as pq

    return json.loads(pq.read_metadata(pa.BufferReader(raw)).metadata[b"geo"])


def test_an_old_units_file_is_upgraded_to_1_1_and_a_current_one_left_alone(monkeypatch, tmp_path, capsys):
    store, uploads = _units_store(monkeypatch, tmp_path, {"M-1": "1.0", "M-2": "1.1"})
    old, current = (f"{vectors.identity.UNITS_PREFIX}/{s}/{s}.units.parquet" for s in ("M-1", "M-2"))
    before = store[current]

    heal = vectors.self_heal({}, units=vectors.units_objects())

    assert heal.units_upgraded == 1 and heal.failed == []
    geo = _geo(store[old])
    assert geo["version"] == "1.1.0" and "bbox" in geo["columns"][geo["primary_column"]]["covering"]
    assert uploads == {old: vectors.gcs.CACHE_MUTABLE}  # rewritten in place, no longer immutable
    assert store[current] == before
    upgraded = gpd.read_parquet(BytesIO(store[old]))  # Hilbert-sorted, so compared as a set
    # The writer records lon/lat WGS84 as GeoParquet's default, OGC:CRS84 (EPSG:4326 in x/y order).
    assert sorted(upgraded["MapUnit"]) == ["Qaf", "Qal"] and upgraded.crs.to_string() == "OGC:CRS84"
    assert "units upgraded: 1" in heal.summary()


def test_a_footer_larger_than_the_first_range_is_read_again_in_full(monkeypatch, tmp_path):
    store, _ = _units_store(monkeypatch, tmp_path, {"M-1": "1.0", "M-2": "1.1"})
    reads = []
    monkeypatch.setattr(vectors.gcs, "get_tail", lambda p, length: reads.append(length) or store[p][-length:])
    monkeypatch.setattr(vectors, "FOOTER_PROBE_BYTES", 16)  # smaller than any footer

    pfx = vectors.identity.UNITS_PREFIX
    assert vectors.units_outdated(f"{pfx}/M-1/M-1.units.parquet") is True
    assert vectors.units_outdated(f"{pfx}/M-2/M-2.units.parquet") is False
    assert reads[0] == 16 and reads[1] > 16 and reads[2] == 16 and reads[3] > 16


def test_an_old_units_file_with_a_plain_bbox_column_is_rewritten_with_a_correct_covering(
        monkeypatch, tmp_path):
    """A 1.0 file can carry a `bbox` struct column with no covering metadata; the writer replaces it
    with bboxes computed from the geometry instead of keeping stale values."""
    store, _ = _units_store(monkeypatch, tmp_path, {})
    gdf = gpd.GeoDataFrame({"MapUnit": ["Qal", "Qaf"]},
                           geometry=[Point(-112.0, 40.0), Point(-112.1, 40.1)], crs="EPSG:4326")
    gdf["bbox"] = [{"xmin": 0.0, "ymin": 0.0, "xmax": 0.0, "ymax": 0.0}] * 2  # stale
    local = tmp_path / "old.parquet"
    gdf.to_parquet(local, schema_version="1.0.0")
    path = f"{vectors.identity.UNITS_PREFIX}/M-9/M-9.units.parquet"
    store[path] = local.read_bytes()

    heal = vectors.self_heal({}, units=vectors.units_objects())

    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pq.read_table(pa.BufferReader(store[path]))
    assert heal.units_upgraded == 1 and table.column_names.count("bbox") == 1
    assert sorted(b["xmin"] for b in table.column("bbox").to_pylist()) == [-112.1, -112.0]
    geo = _geo(store[path])
    assert geo["version"] == "1.1.0" and "bbox" in geo["columns"][geo["primary_column"]]["covering"]


def test_units_files_past_the_budget_are_pending_not_failed(monkeypatch, tmp_path):
    store, uploads = _units_store(monkeypatch, tmp_path, {"M-1": "1.0", "M-2": "1.0"})
    heal = vectors.self_heal({}, units=vectors.units_objects(), deadline=0.0)
    assert (heal.units_upgraded, heal.pending_units, heal.failed, uploads) == (0, 2, [], {})
    assert "0 units files" not in heal.summary() and "2 units files still pending" in heal.summary()


def test_a_units_file_that_cannot_be_read_or_rewritten_is_a_failure_for_its_pub(monkeypatch, tmp_path):
    store, _ = _units_store(monkeypatch, tmp_path, {"M-1": "1.0", "M-2": "1.0"})
    store[f"{vectors.identity.UNITS_PREFIX}/M-1/M-1.units.parquet"] = b"not parquet at all"
    real_download = vectors.gcs.download

    def download(path, dest, **kw):
        if "/M-2/" in path:
            raise PermissionError("403")
        real_download(path, dest, **kw)

    monkeypatch.setattr(vectors.gcs, "download", download)
    heal = vectors.self_heal({}, units=vectors.units_objects())
    assert set(heal.failed_by_series) == {"M-1", "M-2"}
    assert "footer unreadable" in heal.failed_by_series["M-1"][0]
    assert "units not upgraded: PermissionError: 403" in heal.failed_by_series["M-2"][0]


def test_units_objects_lists_per_pub_files_only(monkeypatch, tmp_path):
    store, _ = _units_store(monkeypatch, tmp_path, {"M-1": "1.1", "OFR-5": "1.1"})
    pfx = vectors.identity.UNITS_PREFIX
    store[f"{pfx}/units.parquet"] = b"statewide"
    store[f"{pfx}/M-1/other.parquet"] = b"x"
    assert vectors.units_objects() == {"M-1": f"{pfx}/M-1/M-1.units.parquet",
                                       "OFR-5": f"{pfx}/OFR-5/OFR-5.units.parquet"}
    assert vectors.units_objects({"OFR-5"}) == {"OFR-5": f"{pfx}/OFR-5/OFR-5.units.parquet"}


def test_an_ingest_upgrades_the_units_files_of_the_pubs_it_covers(monkeypatch, tmp_path, capsys):
    store, _ = _units_store(monkeypatch, tmp_path, {"M-1": "1.0", "OFR-5": "1.0"})
    ingest, _ = _ingest(monkeypatch, store, [{"series_id": "M-1", "pub_year": "2020"},
                                             {"series_id": "OFR-5", "pub_year": "2020"}])

    assert ingest.build_catalog(series="M", skip_refresh=True) == 1
    assert _geo(store[f"{vectors.identity.UNITS_PREFIX}/M-1/M-1.units.parquet"])["version"] == "1.1.0"
    assert _geo(store[f"{vectors.identity.UNITS_PREFIX}/OFR-5/OFR-5.units.parquet"])["version"] == "1.0.0"
    assert "units upgraded: 1" in capsys.readouterr().out


def test_time_and_time_zone_columns_survive_into_the_tiles(monkeypatch, tmp_path):
    """GDAL's GeoJSONSeq writer drops TIME columns and strips a timestamp's zone; they are cast to
    ISO strings first."""
    import datetime as dt

    import pyarrow as pa
    import pyarrow.parquet as pq

    _mem_gcs(monkeypatch)
    lines = _capture_geojsonl(monkeypatch)
    src, work = _geoparquet(tmp_path, {"Type": ["a"]}, [Point(424000, 4512000)])
    table = pq.read_table(src)
    table = table.append_column("at", pa.array([dt.time(10, 11, 12)], pa.time64("us")))
    table = table.append_column("stamp", pa.array([dt.datetime(2020, 1, 1, 10, tzinfo=dt.timezone.utc)],
                                                  pa.timestamp("us", tz="UTC")))
    pq.write_table(table.replace_schema_metadata(pq.read_schema(src).metadata), src)
    dropped: list[str] = []

    assert vectors._tile_layer(src, {"label": "t", "proj_code": "EPSG:26912"}, "M-213", work, dropped)

    props = json.loads(lines[0])["properties"]
    assert props["at"] == "10:11:12" and props["stamp"].startswith("2020-01-01 10:00:00")
    assert dropped == []


def test_a_column_missing_from_the_tiles_is_a_warning_naming_it(monkeypatch, tmp_path):
    store = _mem_gcs(monkeypatch)
    src, work = _geoparquet(tmp_path, {"Type": ["a"], "Name": ["n"]}, [Point(424000, 4512000)])
    real = vectors._missing_attributes
    monkeypatch.setattr(vectors, "_missing_attributes",
                        lambda path, cols, types: real(path, [*cols, "Lost"], {**types}))
    manifest = {"warnings": []}
    entry = {"label": "w", "proj_code": "EPSG:26912"}

    assert vectors._tile_into(entry, src, "M-214", work, manifest) is True

    (warning,) = manifest["warnings"]
    assert warning["code"] == vectors.TILE_COLUMNS_DROPPED and "Lost" in warning["warning"]
    assert entry["pmtiles"] is True and vectors.pmtiles_object("M-214", "w") in store


# ---------------------------------------------------------------------------
# Layer styles: written beside the tiles at extraction, and by the self-heal for pubs extracted before
# ---------------------------------------------------------------------------

def _styled_pub(monkeypatch, tmp_path, **layer_extra):
    """A pub with a tiled polygon layer that has no style yet."""
    return _backfill_store(monkeypatch, tmp_path, {"M-500": _manifest(
        {"label": "geo__Polys", "spatial": True, "pmtiles": True, "pmtiles_layer": "geo__Polys",
         "geometry_types": ["MultiPolygon"], **layer_extra})})


def _stored_manifest(store, sid="M-500"):
    return json.loads(store[f"{vectors.VECTORS_PREFIX}/{sid}/_manifest.json"])


@pytest.mark.usefixtures("no_geopackage")
def test_self_heal_styles_a_tiled_layer_with_the_default_look_and_records_it(monkeypatch, tmp_path, capsys):
    store = _styled_pub(monkeypatch, tmp_path)

    assert _heal(["M-500"]) == 0

    style = json.loads(store[vectors.style_object("M-500", "geo__Polys")])
    assert style == vectors.layer_style.default_style("geo__Polys")
    manifest = _stored_manifest(store)
    assert manifest["layers"][0]["style"] == "geo__Polys.style.json"
    assert manifest["style_version"] == vectors.layer_style.STYLE_VERSION
    assert "1 pubs' layer styles written" in capsys.readouterr().out
    assert not vectors.needs_of(manifest).styles  # nothing left for the next run


@pytest.mark.usefixtures("no_geopackage")
def test_a_layer_style_that_cannot_be_written_fails_loud_and_is_retried(monkeypatch, tmp_path):
    store = _styled_pub(monkeypatch, tmp_path)
    put = vectors.gcs.put_bytes

    def refuse(data, path, **kw):
        if path.endswith(".style.json"):
            raise OSError("denied")
        return put(data, path, **kw)

    monkeypatch.setattr(vectors.gcs, "put_bytes", refuse)

    heal = vectors.self_heal({"M-500": vectors.needs_of(vectors._clean_manifest_of("M-500"))})

    assert len(heal.failed) == 1 and heal.failed[0].startswith("M-500/geo__Polys: style not written")
    manifest = _stored_manifest(store)
    assert "style" not in manifest["layers"][0] and "style_version" not in manifest
    assert [w["code"] for w in manifest["warnings"]] == [vectors.STYLE_FAILED]
    assert vectors.needs_of(manifest).styles


@pytest.mark.usefixtures("no_geopackage")
def test_styles_are_rewritten_when_the_style_version_is_bumped(monkeypatch, tmp_path):
    store = _styled_pub(monkeypatch, tmp_path, style="geo__Polys.style.json")
    manifest = _stored_manifest(store)
    manifest["style_version"] = vectors.layer_style.STYLE_VERSION
    assert not vectors.needs_styles(manifest)

    monkeypatch.setattr(vectors.layer_style, "STYLE_VERSION", vectors.layer_style.STYLE_VERSION + 1)
    assert vectors.needs_styles(manifest)
    assert _heal(["M-500"]) == 0
    assert vectors.style_object("M-500", "geo__Polys") in store
    assert _stored_manifest(store)["style_version"] == vectors.layer_style.STYLE_VERSION


@pytest.mark.usefixtures("no_geopackage")
def test_a_layer_without_tiles_is_styled_in_the_heal_that_tiles_it(monkeypatch, tmp_path):
    store = _backfill_store(monkeypatch, tmp_path, {"M-501": _manifest(
        {"label": "todo", "spatial": True, "geometry_types": ["Point"]})})
    assert not vectors.needs_styles(_stored_manifest(store, "M-501"))

    assert _heal(["M-501"]) == 0

    layer = _stored_manifest(store, "M-501")["layers"][0]
    assert layer["style"] == "todo.style.json"
    assert vectors.style_object("M-501", "todo") in store


def test_the_ingest_heal_leaves_styles_to_the_vectors_job(monkeypatch, tmp_path):
    store = _styled_pub(monkeypatch, tmp_path)
    needs = {"M-500": vectors.needs_of(vectors._clean_manifest_of("M-500"))}
    assert needs["M-500"].styles

    heal = vectors.self_heal(needs, tiles=False)

    assert heal.styled == 0 and vectors.style_object("M-500", "geo__Polys") not in store


def test_an_ingest_binds_the_generated_style_to_a_single_file_collection(monkeypatch, tmp_path):
    from ugs_warehouse.core import stac

    bind = stac.attach_renders
    doc = _manifest({"label": "faults", "spatial": True, "pmtiles": True, "pmtiles_layer": "faults",
                     "style": "faults.style.json"})
    doc["style_version"] = vectors.layer_style.STYLE_VERSION
    store = _backfill_store(monkeypatch, tmp_path, {"M-600": doc})
    ingest, written = _ingest(monkeypatch, store, [{"series_id": "M-600", "pub_year": "2020"}])
    monkeypatch.setattr(stac, "attach_renders", bind)
    monkeypatch.setattr(stac.styles, "_manifest", lambda: ())

    assert ingest.build_catalog(skip_refresh=True) == 1

    (coll,) = [d for d in written if d.get("type") == "Collection"]
    style_url = f"/{vectors.VECTORS_PREFIX}/M-600/faults.style.json"
    assert coll["ugs:renders"]["default"]["style_url"].endswith(style_url)
    assert coll["assets"]["style"]["roles"] == ["style"] and "pmtiles" in coll["assets"]


def test_a_clean_manifest_without_a_geopackage_needs_one_from_the_heal():
    manifest = {"layers": [{"label": "geo__DMU", "spatial": False, "rows": 1, "checksum": "1220ab"}],
                "relations_version": vectors.relations.RELATIONS_VERSION}

    assert vectors.needs_of(manifest).geopackage
    manifest["geopackage"] = {"version": vectors.GEOPACKAGE_VERSION,
                              "inputs": vectors._geopackage_inputs(manifest), "bytes": 1}
    assert not vectors.needs_of(manifest).geopackage


@pytest.mark.usefixtures("no_geopackage")
def test_a_layer_named_like_the_geopackage_relationships_table_is_an_error(monkeypatch, tmp_path):
    _mem_gcs(monkeypatch)
    table_df = pd.DataFrame({"a": [1]})
    monkeypatch.setattr("pyogrio.read_dataframe", lambda path, layer=None, read_geometry=True: table_df)

    manifest = vectors._extract_and_upload(
        str(tmp_path), "M-110", [("b.gdb", "x", "Ugs_Relationships", False)])

    assert manifest["tables"] == []
    assert manifest["errors"] == [{"label": "Ugs_Relationships",
                                   "error": "label is reserved for the GeoPackage's ugs_relationships table"}]


# ---------------------------------------------------------------------------
# Task sharding (`--tasks=N`)
# ---------------------------------------------------------------------------

SHARD_SIDS = [f"M-{n}" for n in range(1, 8)]


@pytest.fixture
def shard_run(monkeypatch):
    """An `--all` run over SHARD_SIDS where the first five are clean but need a heal, with
    extraction, the heal, the units listing and identify recorded instead of run."""
    from ugs_warehouse.pubs import source

    seen = {"extract": [], "heal": [], "identify": 0}
    monkeypatch.setattr(source, "read_pubs", lambda: [{"series_id": s} for s in SHARD_SIDS])
    monkeypatch.setattr(vectors.harvest, "data_files", lambda sid: [("Data", "https://x/gis.zip")])
    monkeypatch.setattr(vectors, "_series_with", lambda *names: set())
    monkeypatch.setattr(vectors, "clean_manifests", lambda reduce=None: {
        s: (vectors.Needs((), False, True), True) for s in SHARD_SIDS[:5]})
    monkeypatch.setattr(vectors, "extract_one", lambda sid, **kw: seen["extract"].append(sid) or "ok")
    monkeypatch.setattr(vectors, "units_objects",
                        lambda ids=None: {s: f"u/{s}" for s in SHARD_SIDS
                                          if ids is None or s.upper() in ids})
    monkeypatch.setattr(vectors, "self_heal", lambda needs, **kw: seen["heal"].append(
        (sorted(needs), sorted(kw["units"]))) or vectors.Heal())

    def identify_run(tiers=None):
        seen["identify"] += 1
        return vectors.identify.Result()

    monkeypatch.setattr(vectors.identify, "build_changed", identify_run)
    monkeypatch.setattr("sys.argv", ["vectors", "--all"])
    monkeypatch.delenv("CLOUD_RUN_TASK_COUNT", raising=False)
    monkeypatch.delenv("CLOUD_RUN_TASK_INDEX", raising=False)
    return seen


def _run_task(monkeypatch, seen, index: int, count: int):
    monkeypatch.setenv("CLOUD_RUN_TASK_INDEX", str(index))
    monkeypatch.setenv("CLOUD_RUN_TASK_COUNT", str(count))
    seen["extract"].clear()
    seen["heal"].clear()
    seen["identify"] = 0
    assert vectors.main() == 0
    return list(seen["extract"]), list(seen["heal"]), seen["identify"]


def test_shard_helper_is_a_disjoint_stride_that_keeps_order(monkeypatch):
    from ugs_warehouse.core import jobs

    items = list("abcdefg")
    shards = []
    monkeypatch.setenv("CLOUD_RUN_TASK_COUNT", "3")
    for i in range(3):
        monkeypatch.setenv("CLOUD_RUN_TASK_INDEX", str(i))
        shards.append(jobs.shard(items))
    assert shards == [list("adg"), list("be"), list("cf")]
    assert sorted(x for s in shards for x in s) == items
    monkeypatch.setenv("CLOUD_RUN_TASK_COUNT", "1")
    monkeypatch.setenv("CLOUD_RUN_TASK_INDEX", "0")
    assert jobs.shard(items) is items


def test_an_unsharded_run_extracts_and_heals_everything(monkeypatch, shard_run):
    extracted, heals, identify_runs = _run_task(monkeypatch, shard_run, 0, 1)
    assert extracted == SHARD_SIDS
    assert heals == [(SHARD_SIDS[:5], SHARD_SIDS)]
    assert identify_runs == 1


def test_sharded_tasks_extract_and_heal_disjoint_sets_owned_by_series_id(monkeypatch, shard_run):
    out = [_run_task(monkeypatch, shard_run, i, 3) for i in range(3)]
    for i, (extracted, heals, _) in enumerate(out):
        healed, units = heals[0]
        # Every pub a task extracts, heals or upgrades is one it owns by series id.
        assert all(jobs_owner(s) == i for s in [*extracted, *healed, *units])
    assert sorted(s for o in out for s in o[0]) == sorted(SHARD_SIDS)
    assert sorted(s for o in out for s in o[1][0][1]) == sorted(SHARD_SIDS)


def jobs_owner(sid: str, n: int = 3) -> int:
    import zlib
    return zlib.crc32(sid.upper().encode()) % n


def test_ownership_does_not_depend_on_list_order_or_state(monkeypatch):
    from ugs_warehouse.core import jobs
    monkeypatch.setenv("CLOUD_RUN_TASK_COUNT", "3")
    owners = {}
    for i in range(3):
        monkeypatch.setenv("CLOUD_RUN_TASK_INDEX", str(i))
        for sid in ("M-1", "OFR-784DM", "ds-5", "DS-5"):
            if jobs.owns(sid):
                owners.setdefault(sid.upper(), set()).add(i)
    assert all(len(v) == 1 for v in owners.values())  # exactly one owner, case-insensitive


def test_identify_runs_only_in_task_zero(monkeypatch, shard_run):
    assert [_run_task(monkeypatch, shard_run, i, 3)[2] for i in range(3)] == [1, 0, 0]


def test_a_targeted_run_is_not_sharded(monkeypatch, shard_run):
    monkeypatch.setattr("sys.argv", ["vectors", "M-1", "M-2", "M-3"])
    monkeypatch.setattr(vectors, "_clean_manifest_of", lambda sid: None)
    extracted, heals, identify_runs = _run_task(monkeypatch, shard_run, 1, 3)
    assert extracted == ["M-1", "M-2", "M-3"]
    assert heals[0][1] == ["M-1", "M-2", "M-3"]
    assert identify_runs == 0
