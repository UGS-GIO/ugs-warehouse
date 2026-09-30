from __future__ import annotations

import os
import sys

import pytest

try:
    import django  # noqa: F401
    HAS_DJANGO = True
except ImportError:
    HAS_DJANGO = False

if HAS_DJANGO:
    # Add the admin directory to sys.path so its internal modules can be imported
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../admin")))
    from ops import stac
else:
    stac = None


@pytest.mark.skipif(not HAS_DJANGO, reason="Django not installed in this environment")
def test_get_harvest_status():
    mock_pubs = [
        {"series_id": "OFR-593", "pub_name": "Rush Valley Geologic Map", "pub_scale": "1:62,500"},
        {"series_id": "M-94", "pub_name": "Geologic map of some place", "pub_scale": "1:24,000"},
        # Placeholder convention is XXXX in the SERIES ID (see harvest.py / vectors.py), not the name.
        {"series_id": "SS-XXXX", "pub_name": "Unpublished Placeholder", "pub_scale": ""},
    ]
    mock_attachments = [
        {"series_id": "OFR-593", "pub_url": "open_file_reports/ofr-593/ofr-593_plates.zip"},
        {"series_id": "M-94", "pub_url": "geologicmaps/M-94_text.pdf"},  # PDF-only
    ]
    mock_gcs_cogs = [
        "geolmap/cogs/OFR-593.cog.tif",
    ]

    from unittest.mock import patch
    with patch("ugs_warehouse.pubs.source.read_pubs", return_value=mock_pubs), \
         patch("ugs_warehouse.pubs.source.read_attachments", return_value=mock_attachments), \
         patch("ugs_warehouse.core.gcs.list_paths", return_value=mock_gcs_cogs):

        # Test unfiltered status query
        statuses = stac.get_harvest_status()
        assert len(statuses) == 3

        ofr = next(s for s in statuses if s["id"] == "OFR-593")
        assert ofr["status"] == "harvested"
        assert len(ofr["zips"]) == 1
        assert ofr["zips"][0]["name"] == "ofr-593_plates.zip"

        m94 = next(s for s in statuses if s["id"] == "M-94")
        assert m94["status"] == "pdf_only"
        assert len(m94["zips"]) == 0

        # Test search filter
        ofr_filtered = stac.get_harvest_status(search_query="OFR-593")
        assert len(ofr_filtered) == 1
        assert ofr_filtered[0]["id"] == "OFR-593"

        # Test status filter
        pdf_filtered = stac.get_harvest_status(status_filter="pdf_only")
        assert len(pdf_filtered) == 1
        assert pdf_filtered[0]["id"] == "M-94"


@pytest.mark.skipif(not HAS_DJANGO, reason="Django not installed in this environment")
def test_console_cannot_rebuild_the_24k_mosaic_on_cloud_run():
    """24k runs on Cloud Batch at z17; a Cloud Run rebuild would overwrite it with a z14 mosaic."""
    from ops import jobs
    assert {t for t, _ in jobs.JOBS["mosaics"].tiers} == {"250k", "500k"}
    res = jobs.rebuild_mosaic("24k")
    assert res["ok"] is False and "unknown mosaic tier" in res["message"]
