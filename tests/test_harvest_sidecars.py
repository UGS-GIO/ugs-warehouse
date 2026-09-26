"""Georeferencing sidecars are found regardless of filename case.

The zip extractor accepts any case, so an uppercase FOO.TFW / FOO.PRJ lands on disk. The lookups
used exact case, so those bundles reported `wf=False srs=False` and gdalwarp then failed with
"Cannot compute bounding box of cutline. Cannot find source SRS" — 14 pubs in one backfill run.

NOTE: macOS filesystems are case-insensitive, so the real-file tests below pass even on the old
exact-case code locally; they only bite on Linux (CI, and Cloud Run). The listdir-driven tests are
the ones that prove the matching logic everywhere.
"""
from __future__ import annotations

import os

import pytest

from ugs_warehouse.pubs import harvest


@pytest.fixture()
def bundle(tmp_path):
    (tmp_path / "plate.tif").write_bytes(b"")
    return tmp_path


@pytest.mark.parametrize("name", ["plate.tfw", "PLATE.TFW", "plate.TFW", "Plate.Tfw"])
def test_world_file_found_in_any_case(bundle, name):
    (bundle / name).write_text("1 0 0 -1 0 0")
    found = harvest._sidecar(str(bundle / "plate"), (".tfwx", ".tfw", ".wld"))
    assert found is not None
    assert os.path.basename(found) == name


@pytest.mark.parametrize("ext", [".tfwx", ".tfw", ".wld"])
def test_each_world_file_extension_is_accepted(bundle, ext):
    (bundle / f"plate{ext.upper()}").write_text("1 0 0 -1 0 0")
    assert harvest._sidecar(str(bundle / "plate"), (".tfwx", ".tfw", ".wld")) is not None


def test_aux_xml_found_in_any_case(bundle):
    (bundle / "plate.tif.AUX.XML").write_text("<PAMDataset/>")
    found = harvest._sidecar(str(bundle / "plate.tif"), (".aux.xml",))
    assert found is not None


def test_missing_sidecar_still_returns_none(bundle):
    assert harvest._sidecar(str(bundle / "plate"), (".tfw",)) is None


def test_unreadable_directory_returns_none(tmp_path):
    assert harvest._sidecar(str(tmp_path / "nope" / "plate"), (".tfw",)) is None


def test_prj_srs_reads_an_uppercase_prj(tmp_path):
    """_prj_srs globbed '*.prj', so an uppercase .PRJ shipped with a shapefile was invisible."""
    wkt = (
        'GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",SPHEROID["WGS_1984",6378137.0,298.257223563]],'
        'PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]]'
    )
    sub = tmp_path / "gis"
    sub.mkdir()
    (sub / "OUTLINE.PRJ").write_text(wkt)
    pytest.importorskip("rasterio")
    assert harvest._prj_srs(str(tmp_path)) is not None


def test_prj_srs_returns_none_when_absent(tmp_path):
    pytest.importorskip("rasterio")
    assert harvest._prj_srs(str(tmp_path)) is None


# --- filesystem-independent: prove the MATCHING is case-insensitive, not the filesystem -----------

def test_sidecar_matching_is_case_insensitive_regardless_of_filesystem(monkeypatch):
    monkeypatch.setattr(harvest.os, "listdir", lambda _d: ["PLATE.TFW", "readme.txt"])
    found = harvest._sidecar("/bundle/plate", (".tfwx", ".tfw", ".wld"))
    assert found == os.path.join("/bundle", "PLATE.TFW")


def test_sidecar_matches_a_mixed_case_stem(monkeypatch):
    monkeypatch.setattr(harvest.os, "listdir", lambda _d: ["Plate.Wld"])
    assert harvest._sidecar("/bundle/PLATE", (".tfw", ".wld")) is not None


def test_sidecar_does_not_match_a_different_stem(monkeypatch):
    """Case-insensitive must not mean loose — a different basename is still a miss."""
    monkeypatch.setattr(harvest.os, "listdir", lambda _d: ["OTHER.TFW"])
    assert harvest._sidecar("/bundle/plate", (".tfw",)) is None


def test_sidecar_does_not_match_a_prefix(monkeypatch):
    monkeypatch.setattr(harvest.os, "listdir", lambda _d: ["plate_v2.tfw"])
    assert harvest._sidecar("/bundle/plate", (".tfw",)) is None


def test_source_crs_georeferences_a_plate_with_only_a_world_file(tmp_path, monkeypatch):
    pytest.importorskip("rasterio")
    import numpy as np
    import rasterio

    tif = tmp_path / "plate.tif"
    with rasterio.open(tif, "w", driver="GTiff", width=4, height=4, count=1, dtype="uint8") as ds:
        ds.write(np.zeros((1, 4, 4), "uint8"))
    (tmp_path / "plate.tfw").write_text("5\n0\n0\n-5\n245681\n4654971\n")
    monkeypatch.setitem(harvest.SOURCE_CRS, "OFR-TEST", "EPSG:26712")
    monkeypatch.setattr(harvest, "run", lambda cmd: __import__("subprocess").run(cmd, check=True))
    harvest._series_ctx.set("OFR-TEST")

    with rasterio.open(harvest.corrected_georef(str(tif), str(tmp_path))) as ds:
        assert ds.crs.to_epsg() == 26712
        assert ds.bounds.left == pytest.approx(245678.5)
