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

    srcs = vectors._sources(str(tmp_path))

    assert srcs == [
        (str(gdb), "ContactsAndFaults", "GeologicMap__ContactsAndFaults", True),
        (str(gdb), "DescriptionOfMapUnits", "GeologicMap__DescriptionOfMapUnits", False),
    ]


def test_sources_treats_shapefiles_as_always_spatial(tmp_path):
    (tmp_path / "faults.shp").write_bytes(b"")

    srcs = vectors._sources(str(tmp_path))

    assert srcs == [(str(tmp_path / "faults.shp"), None, "faults", True)]


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

    assert manifest == {
        "spatial": ["geo__ContactsAndFaults"],
        "tables": ["geo__DescriptionOfMapUnits"],
    }

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

    assert manifest == {"spatial": [], "tables": []}
    # No layer uploaded, but an empty manifest marks the pub done so the next run skips the zip.
    assert store == {f"{vectors.VECTORS_PREFIX}/M-101/_manifest.json":
                     json.dumps({"spatial": [], "tables": []}).encode()}


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

    assert manifest == {"spatial": [], "tables": ["geo__DescriptionOfMapUnits"]}
    # The failed layer never reached GCS; the table that succeeded did, plus the manifest.
    assert store.keys() == {
        f"{vectors.VECTORS_PREFIX}/M-102/geo__DescriptionOfMapUnits.parquet",
        f"{vectors.VECTORS_PREFIX}/M-102/_manifest.json",
    }


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
    downloads: list[str] = []
    monkeypatch.setattr(harvest, "download", lambda url, dst: downloads.append(url) or 1 / 0)
    monkeypatch.setattr(sys, "argv", ["vectors", "--all"])

    vectors.main()

    assert downloads == ["https://x/m2-gis.zip"]
    assert listings == [f"{vectors.VECTORS_PREFIX}/"]
