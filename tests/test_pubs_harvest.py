from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import pytest

from ugs_warehouse.pubs.harvest import ZipTooLargeError, _harvest_attempt, download, identity

try:
    import rasterio  # noqa: F401
    import rio_cogeo  # noqa: F401
    HAS_RASTER_DEPS = True
except ImportError:
    HAS_RASTER_DEPS = False


def test_download_checks_content_length():
    mock_response = MagicMock()
    mock_response.headers = {"Content-Length": "1000"}

    with patch("ugs_warehouse.pubs.harvest.S") as mock_session:
        mock_session.get.return_value.__enter__.return_value = mock_response

        # Expect ZipTooLargeError because content length (1000) exceeds max_bytes (500)
        with pytest.raises(ZipTooLargeError) as exc_info:
            download("http://fake.com/test.zip", "dest.zip", max_bytes=500)
        assert "exceeds limit" in str(exc_info.value)


def test_download_checks_stream_progress():
    mock_response = MagicMock()
    mock_response.headers = {}  # No content length header
    mock_response.iter_content.return_value = [b"a" * 300, b"b" * 300]

    with patch("ugs_warehouse.pubs.harvest.S") as mock_session:
        mock_session.get.return_value.__enter__.return_value = mock_response

        # Expect ZipTooLargeError because streamed bytes exceed max_bytes (500)
        with patch("builtins.open", unittest.mock.mock_open()):
            with pytest.raises(ZipTooLargeError) as exc_info:
                download("http://fake.com/test.zip", "dest.zip", max_bytes=500)
            assert "downloaded bytes exceeded limit" in str(exc_info.value)


@pytest.mark.skipif(not HAS_RASTER_DEPS, reason="requires rio_cogeo and rasterio")
def test_harvest_attempt_handles_zip_too_large():
    pub = identity.Pub(series_id="OFR-593")

    with patch("ugs_warehouse.pubs.harvest.footprint") as mock_footprint, \
         patch("ugs_warehouse.pubs.harvest.download") as mock_download:
        mock_footprint.return_value = ("cut.geojson", 1)
        # Mock download to raise ZipTooLargeError
        mock_download.side_effect = ZipTooLargeError("Too big!")

        res = _harvest_attempt(pub, ["http://fake.com/huge.zip"])
        assert res == "skip:too_large"


@pytest.mark.skipif(not HAS_RASTER_DEPS, reason="requires rio_cogeo and rasterio")
def test_prepare_plates_virtual_vfs():
    from ugs_warehouse.pubs.harvest import prepare_plates

    mock_zip_instance = MagicMock()
    mock_zip_instance.namelist.return_value = ["plate1.tif", "plate1.tfw"]

    with patch("zipfile.ZipFile") as mock_zipfile, \
         patch("ugs_warehouse.pubs.harvest.corrected_georef") as mock_corrected_georef:
        mock_zipfile.return_value.__enter__.return_value = mock_zip_instance
        mock_corrected_georef.return_value = "mocked_gtif_path"

        plate, shp = prepare_plates(["/tmp/test.zip"], "/tmp/work")

        assert plate == "mocked_gtif_path"
        assert shp is None

        # Verify that zip extract was called with the sidecar tfw, but NOT the massive tif!
        mock_zip_instance.extract.assert_called_once_with("plate1.tfw", "/tmp/work")

        # Verify that corrected_georef was called with the correct /vsizip/ path!
        mock_corrected_georef.assert_called_once_with(
            "/vsizip//tmp/test.zip/plate1.tif",
            "/tmp/work",
            zip_path="/tmp/test.zip",
            inner_gtif="plate1.tif"
        )


def test_get_attached_zips():
    from ugs_warehouse.pubs.harvest import _attachments_cache, _get_attached_zips

    # Clear the global cache to force reading from mock
    _attachments_cache.clear()

    mock_attachments = [
        {"series_id": "OFR-593", "pub_url": "open_file_reports/ofr-593/ofr-593_plates.zip", "extra_data": "GeoTIFF - Zip"},
        {"series_id": "OFR-593", "pub_url": "open_file_reports/ofr-593/ofr-593.zip", "extra_data": "GIS Data - Zip"},
        {"series_id": "M-94", "pub_url": "geologicmaps/M-94_text.pdf", "extra_data": "Text - PDF"},
    ]

    with patch("ugs_warehouse.pubs.source.read_attachments", return_value=mock_attachments):
        # Resolve attachments for OFR-593
        gt, gis = _get_attached_zips("OFR-593")
        assert gt == "https://ugspub.nr.utah.gov/publications/open_file_reports/ofr-593/ofr-593_plates.zip"
        assert gis == "https://ugspub.nr.utah.gov/publications/open_file_reports/ofr-593/ofr-593.zip"

        # Resolve attachments for M-94 (which is PDF only, no zips)
        gt, gis = _get_attached_zips("M-94")
        assert gt is None
        assert gis is None


def test_harvest_refuses_force_overwrite_of_published_cog(monkeypatch):
    from ugs_warehouse.pubs import harvest
    monkeypatch.setattr(harvest.gcs, "exists", lambda p: True)
    assert harvest.harvest_one("M-299DM", force=True) == "fail:write-once"
