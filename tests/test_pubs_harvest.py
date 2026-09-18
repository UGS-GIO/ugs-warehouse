from __future__ import annotations

import os
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
    monkeypatch.setattr(harvest, "_pdf_has_crs", lambda path: (True, False))  # opened, no CRS

    with patch("zipfile.ZipFile") as mock_zipfile:
        mock_zipfile.return_value.__enter__.return_value = MagicMock()
        result = harvest._render_geospatial_pdf([("/tmp/test.zip", "M-1_Plate1.pdf")], str(tmp_path))

    assert result is None


@pytest.mark.skipif(not HAS_RASTER_DEPS, reason="requires rio_cogeo and rasterio")
def test_render_geospatial_pdf_renders_georeferenced_candidate(monkeypatch, tmp_path):
    """A georeferenced plate PDF (GDAL reads a CRS) is rasterized via gdal_translate at COG_DPI,
    using the PDF's own georeferencing (-oo DPI=…) rather than the GeoTIFF's bounds."""
    from ugs_warehouse.pubs import harvest

    monkeypatch.setattr(harvest, "COG_DPI", 600)
    monkeypatch.setattr(harvest, "_pdf_has_crs", lambda path: (True, True))  # opened, georeferenced

    def fake_run(cmd):
        open(cmd[5], "wb").close()  # simulate gdal_translate writing the rendered output

    mock_run = MagicMock(side_effect=fake_run)
    monkeypatch.setattr(harvest, "run", mock_run)

    with patch("zipfile.ZipFile") as mock_zipfile:
        mock_zipfile.return_value.__enter__.return_value = MagicMock()
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


# --- harvest run report -----------------------------------------------------------------------

@pytest.mark.skipif(not HAS_RASTER_DEPS, reason="requires rio_cogeo and rasterio")
def test_pdf_has_crs_reads_cli_gdalinfo(monkeypatch):
    """_pdf_has_crs uses the CLI gdalinfo (the harvest image's rasterio wheel has no PDF driver):
    georeferenced -> (True, True); non-georeferenced (no coordinateSystem key) -> (True, False);
    gdalinfo error -> (False, False); missing binary / exception -> (False, False)."""
    from ugs_warehouse.pubs import harvest

    def fake(cmd, **kw):
        r = MagicMock()
        name = cmd[-1]
        if name == "geo.pdf":
            r.returncode, r.stdout = 0, '{"coordinateSystem": {"wkt": "PROJCRS"}}'
        elif name == "plain.pdf":
            r.returncode, r.stdout = 0, '{"size": [100, 100]}'      # opened, no coordinateSystem
        else:
            r.returncode, r.stdout = 1, ""                          # gdalinfo couldn't read it
        return r

    monkeypatch.setattr(harvest.subprocess, "run", fake)
    assert harvest._pdf_has_crs("geo.pdf") == (True, True)
    assert harvest._pdf_has_crs("plain.pdf") == (True, False)
    assert harvest._pdf_has_crs("bad.pdf") == (False, False)

    monkeypatch.setattr(harvest.subprocess, "run",
                        MagicMock(side_effect=FileNotFoundError("gdalinfo")))
    assert harvest._pdf_has_crs("geo.pdf") == (False, False)


@pytest.mark.skipif(not HAS_RASTER_DEPS, reason="requires rio_cogeo and rasterio")
def test_render_geospatial_pdf_advises_when_backend_missing(monkeypatch, tmp_path, capsys):
    """Regression for the live incident: when GDAL can open NO candidate PDF (opened=False — the
    image's PDF driver/backend missing), _render_geospatial_pdf returns None and logs the loud
    'GDAL opened 0 of N' advisory so the z16 fallback is never silent."""
    from ugs_warehouse.pubs import harvest

    monkeypatch.setattr(harvest, "COG_DPI", 600)
    harvest._report_begin("M-1")
    monkeypatch.setattr(harvest, "_pdf_has_crs", lambda path: (False, False))  # cannot open any PDF

    with patch("zipfile.ZipFile") as mock_zipfile:
        mock_zipfile.return_value.__enter__.return_value = MagicMock()
        result = harvest._render_geospatial_pdf([("/tmp/t.zip", "M-1_Plate1.pdf")], str(tmp_path))

    assert result is None
    assert "GDAL opened 0 of 1 candidate PDF" in capsys.readouterr().out


def _fake_pdf_has_crs(geo_basenames):
    """_pdf_has_crs replacement: a PDF is 'georeferenced' iff its basename is in the set (always
    opened — the harvest image's CLI GDAL has the PDF driver)."""
    def _check(path):
        return True, os.path.basename(path) in geo_basenames
    return _check


_BUNDLE = ["M-1_Plate1.pdf", "M-1_Plate2.pdf", "M-1_Booklet.pdf", "M-1_geotiff.tif",
           "M-1_geotiff.tfw", "M-1_geotiff.prj", "M-1_units.shp", "M-1_units.dbf", "M-1.mpk"]


@pytest.mark.skipif(not HAS_RASTER_DEPS, reason="requires rio_cogeo and rasterio")
def test_report_geo_pdf_tier(monkeypatch, tmp_path):
    """A georeferenced plate wins → report tier=geo-pdf, the plate is `used`, and the GeoTIFF is
    `skipped` as superseded — the found-vs-used reconciliation for the good path."""
    from ugs_warehouse.pubs import harvest

    monkeypatch.setattr(harvest, "COG_DPI", 600)
    harvest._report_begin("M-1")
    mock_zip = MagicMock()
    mock_zip.namelist.return_value = _BUNDLE

    def fake_run(cmd):
        open(cmd[5], "wb").close()  # gdal_translate writes the rendered plate

    monkeypatch.setattr(harvest, "run", fake_run)
    monkeypatch.setattr(harvest, "_pdf_has_crs", _fake_pdf_has_crs({"M-1_Plate1.pdf"}))
    with patch("zipfile.ZipFile") as mock_zipfile:
        mock_zipfile.return_value.__enter__.return_value = mock_zip
        plate, shp = harvest.prepare_plates(["/tmp/m-1.zip"], str(tmp_path))

    assert plate == str(tmp_path / "plate_render.tif")
    rep = harvest._pub_report
    assert rep["tier"] == "geo-pdf"
    assert rep["used"]["plate"] == {"name": "M-1_Plate1.pdf", "dpi": 600}
    assert rep["used"]["units_shp"] == "M-1_units.shp"
    plate1 = next(e for e in rep["found"] if e["name"] == "M-1_Plate1.pdf")
    assert plate1 == {"name": "M-1_Plate1.pdf", "kind": "pdf", "georeferenced": True}
    skipped = {s["name"]: s["reason"] for s in rep["skipped"]}
    assert skipped["M-1_geotiff.tif"] == "superseded by geo-PDF"
    assert skipped["M-1.mpk"] == "unsupported (Esri map package)"
    assert "M-1_Plate2.pdf" in skipped  # a non-chosen plate is accounted for, not dropped


@pytest.mark.skipif(not HAS_RASTER_DEPS, reason="requires rio_cogeo and rasterio")
def test_report_geotiff_fallback_tier(monkeypatch, tmp_path):
    """No PDF carries a CRS → report tier=geotiff, the GeoTIFF is `used`, and every PDF is `skipped`
    with reason 'no CRS' — the audit trail that makes a z16 fallback explicit."""
    from ugs_warehouse.pubs import harvest

    monkeypatch.setattr(harvest, "COG_DPI", 600)
    monkeypatch.setattr(harvest, "corrected_georef", lambda *a, **k: "mocked_gtif")
    harvest._report_begin("M-1")
    mock_zip = MagicMock()
    mock_zip.namelist.return_value = _BUNDLE

    monkeypatch.setattr(harvest, "_pdf_has_crs", _fake_pdf_has_crs(set()))  # nothing georeferenced
    with patch("zipfile.ZipFile") as mock_zipfile:
        mock_zipfile.return_value.__enter__.return_value = mock_zip
        plate, shp = harvest.prepare_plates(["/tmp/m-1.zip"], str(tmp_path))

    assert plate == "mocked_gtif"
    rep = harvest._pub_report
    assert rep["tier"] == "geotiff"
    assert rep["used"]["plate"] == {"name": "M-1_geotiff.tif", "dpi": None}
    skipped = {s["name"]: s["reason"] for s in rep["skipped"]}
    for pdf in ("M-1_Plate1.pdf", "M-1_Plate2.pdf", "M-1_Booklet.pdf"):
        assert skipped[pdf] == "no CRS"


@pytest.mark.skipif(not HAS_RASTER_DEPS, reason="requires rio_cogeo and rasterio")
def test_report_records_produced_cog(monkeypatch, tmp_path):
    """The produced side: after a successful attempt the report captures the COG object, size, and
    (lossless) compression, plus the source zip(s) the attempt drew from."""
    from ugs_warehouse.pubs import harvest

    pub = identity.Pub(series_id="M-1")
    harvest._report_begin("M-1")
    monkeypatch.setattr(harvest, "THUMBS", False)
    monkeypatch.setattr(harvest, "footprint", lambda sid, work: ("cut.geojson", 1))
    monkeypatch.setattr(harvest, "download", lambda *a, **k: None)
    monkeypatch.setattr(harvest, "prepare_plates", lambda zips, work: (str(tmp_path / "plate.tif"), None))
    monkeypatch.setattr(harvest, "ensure_rgb", lambda p: p)
    monkeypatch.setattr(harvest, "run", lambda cmd: None)  # gdalwarp no-op

    def fake_cog_translate(src, dst, prof, **k):
        with open(dst, "wb") as f:
            f.write(b"x" * 200_000)

    monkeypatch.setattr("rio_cogeo.cogeo.cog_translate", fake_cog_translate)
    monkeypatch.setattr("rio_cogeo.cogeo.cog_validate", lambda p: (True, [], []))
    monkeypatch.setattr(harvest.gcs, "upload_write_once", lambda *a, **k: None)
    monkeypatch.setattr(harvest.gcs, "upload", lambda *a, **k: None)

    res = _harvest_attempt(pub, ["http://x/m-1.zip"])

    assert res == "ok"
    rep = harvest._pub_report
    assert rep["source_zips"] == ["http://x/m-1.zip"]
    cog = rep["produced"]["cog"]
    assert cog is not None
    assert cog["object"] == pub.cog_object
    assert cog["compress"].lower() == "deflate"  # lossless master, not webp
