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


@pytest.mark.skipif(not HAS_RASTER_DEPS, reason="requires rio_cogeo and rasterio")
def test_render_geospatial_pdf_returns_none_when_no_crs(monkeypatch, tmp_path):
    """A PDF with no embedded CRS (e.g. a plain print-layout PDF) isn't a geospatial plate —
    _render_geospatial_pdf must skip it so the caller falls back to the GeoTIFF."""
    from ugs_warehouse.pubs import harvest

    monkeypatch.setattr(harvest, "COG_DPI", 600)
    mock_ds = MagicMock()
    mock_ds.crs = None

    with patch("zipfile.ZipFile") as mock_zipfile, patch("rasterio.open") as mock_open:
        mock_zipfile.return_value.__enter__.return_value = MagicMock()
        mock_open.return_value.__enter__.return_value = mock_ds

        result = harvest._render_geospatial_pdf([("/tmp/test.zip", "M-1_Plate1.pdf")], str(tmp_path))

    assert result is None


@pytest.mark.skipif(not HAS_RASTER_DEPS, reason="requires rio_cogeo and rasterio")
def test_render_geospatial_pdf_renders_georeferenced_candidate(monkeypatch, tmp_path):
    """A georeferenced plate PDF (GDAL reads a CRS) is rasterized via gdal_translate at COG_DPI,
    using the PDF's own georeferencing (-oo DPI=…) rather than the GeoTIFF's bounds."""
    from ugs_warehouse.pubs import harvest

    monkeypatch.setattr(harvest, "COG_DPI", 600)
    mock_ds = MagicMock()
    mock_ds.crs = MagicMock()  # truthy CRS -> georeferenced

    def fake_run(cmd):
        open(cmd[5], "wb").close()  # simulate gdal_translate writing the rendered output

    mock_run = MagicMock(side_effect=fake_run)
    monkeypatch.setattr(harvest, "run", mock_run)

    with patch("zipfile.ZipFile") as mock_zipfile, patch("rasterio.open") as mock_open:
        mock_zipfile.return_value.__enter__.return_value = MagicMock()
        mock_open.return_value.__enter__.return_value = mock_ds

        result = harvest._render_geospatial_pdf([("/tmp/test.zip", "M-1_Plate1.pdf")], str(tmp_path))

    expected = str(tmp_path / "plate_render.tif")
    assert result == expected
    assert (tmp_path / "plate_render.tif").exists()

    cmd = mock_run.call_args[0][0]
    assert cmd[0] == "gdal_translate"
    assert cmd[cmd.index("-oo") + 1] == "DPI=600"


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


def test_harvest_one_does_not_retry_after_first_attempt_already_uploaded_cog(monkeypatch):
    """First attempt uploads the write-once COG, then fails in a LATER step (units/thumbnail). The
    GT-fallback retry must not fire once the COG already exists — it would re-hit upload_write_once
    on the now-published object (WriteOnceViolation), wasting a full reprocess and emitting a bogus
    write-once failure for a pub whose authoritative COG actually landed. The real first-failure
    reason must be what's returned, and the retry must not even be attempted."""
    from ugs_warehouse.pubs import harvest

    cog_uploaded = {"value": False}

    def fake_attempt(pub, zurls):
        if not cog_uploaded["value"]:
            # First attempt: "uploads" the COG, then fails in a later derivative step.
            cog_uploaded["value"] = True
            return "fail:DuckDBIOException"
        # Only reachable if the retry guard regresses — mirrors the real WriteOnceViolation path.
        return "fail:WriteOnceViolation"

    attempt_mock = MagicMock(side_effect=fake_attempt)
    monkeypatch.setattr(harvest, "manifest_urls", lambda sid: ("https://x/gt.zip", "https://x/gis.zip"))
    monkeypatch.setattr(harvest.gcs, "exists", lambda p: cog_uploaded["value"])
    monkeypatch.setattr(harvest, "_harvest_attempt", attempt_mock)

    result = harvest.harvest_one("M-1")

    assert result == "fail:DuckDBIOException"  # the real first-failure reason, not write-once
    assert attempt_mock.call_count == 1  # no wasted/misleading retry once the COG is already live
