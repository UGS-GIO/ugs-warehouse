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


def _stub_bundle(monkeypatch, tmp_path, srcs_and_errors):
    """extract_one with the download and unzip stubbed out and _sources returning a fixed answer."""
    from ugs_warehouse.pubs import harvest

    monkeypatch.setattr(harvest, "zip_urls", lambda sid: (None, "https://x/gis.zip"))
    monkeypatch.setattr(harvest, "download", lambda url, dst: open(dst, "wb").close())
    monkeypatch.setattr(vectors.zipfile, "ZipFile", lambda p: _NoopZip())
    monkeypatch.setattr(vectors, "_sources", lambda work, nested=(): srcs_and_errors)
    monkeypatch.setattr(vectors.gcs, "list_paths", lambda prefix: [])


class _NoopZip:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def infolist(self):
        return []

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
    monkeypatch.setattr(harvest, "zip_urls", lambda sid: (None, "https://x/gis.zip"))

    def _timeout(url, dst):
        raise TimeoutError("read timed out")

    monkeypatch.setattr(harvest, "download", _timeout)

    assert vectors.extract_one("M-112", force=True) == "fail:TimeoutError"
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
    monkeypatch.setattr(harvest, "_get_attached_zips", lambda sid: (None, f"https://x/{sid}.zip"))
    monkeypatch.setattr(vectors.gcs, "list_paths", lambda prefix: [f"{pfx}/SS-161/_attempt.json"])
    monkeypatch.setattr(vectors.gcs, "get_bytes", lambda p: json.dumps(
        {"result": "unsupported", "errors": [{"source": "Ferron.mpk", "error": "unsupported format"}]}).encode())
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
    monkeypatch.setattr(harvest, "_get_attached_zips", lambda sid: (None, f"https://x/{sid}.zip"))
    monkeypatch.setattr(vectors.gcs, "list_paths", lambda prefix: [f"{pfx}/M-1DM/_attempt.json"])
    monkeypatch.setattr(vectors.gcs, "get_bytes", lambda p: b'{"result": "skip"}')
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
    monkeypatch.setattr(harvest, "_get_attached_zips", lambda sid: (None, gis.get(sid)))
    listings: list[str] = []

    def list_paths(prefix):
        listings.append(prefix)
        return [f"{vectors.VECTORS_PREFIX}/M-1DM/_manifest.json"]

    monkeypatch.setattr(vectors.gcs, "list_paths", list_paths)
    monkeypatch.setattr(vectors.gcs, "get_bytes",
                        lambda p: b'{"spatial": ["a"], "layers": [], "errors": []}')
    monkeypatch.setattr(vectors.gcs, "put_bytes", lambda b, p, **kw: None)
    downloads: list[str] = []
    monkeypatch.setattr(harvest, "download", lambda url, dst: downloads.append(url) or 1 / 0)
    monkeypatch.setattr(sys, "argv", ["vectors", "--all"])

    vectors.main()

    assert downloads == ["https://x/m2-gis.zip"]
    assert set(listings) == {f"{vectors.VECTORS_PREFIX}/"}  # listings only, no per-pub lookups


def test_a_batch_run_tries_new_pubs_before_retrying_failed_ones(monkeypatch):
    """A pub whose last run failed is retried after every pub never tried, so a slow failure
    cannot use up the job's timeout before new pubs are reached."""
    import sys

    from ugs_warehouse.pubs import harvest, source

    pfx = vectors.VECTORS_PREFIX
    monkeypatch.setattr(source, "read_pubs", lambda: [
        {"series_id": "M-1DM"}, {"series_id": "M-2DM"}, {"series_id": "M-3DM"}])
    monkeypatch.setattr(harvest, "_get_attached_zips", lambda sid: (None, f"https://x/{sid}.zip"))
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
    monkeypatch.setattr(harvest, "zip_urls", lambda sid: (None, "https://x/gis.zip"))

    def _timeout(url, dst):
        raise TimeoutError("read timed out")

    monkeypatch.setattr(harvest, "download", _timeout)

    assert vectors.extract_one("M-111", force=True) == "fail:TimeoutError"
    doc = json.loads(store[f"{vectors.VECTORS_PREFIX}/M-111/_attempt.json"])
    assert doc["result"] == "fail:TimeoutError"
    assert doc["errors"] == [{"source": "gis zip", "error": "TimeoutError: read timed out"}]
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
