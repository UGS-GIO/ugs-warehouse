from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch
import pytest

from ugs_warehouse.pubs.harvest import ZipTooLargeError, download, _harvest_attempt, identity


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


def test_harvest_attempt_handles_zip_too_large():
    pub = identity.Pub(series_id="OFR-593")

    with patch("ugs_warehouse.pubs.harvest.footprint") as mock_footprint, \
         patch("ugs_warehouse.pubs.harvest.download") as mock_download:
        mock_footprint.return_value = ("cut.geojson", 1)
        # Mock download to raise ZipTooLargeError
        mock_download.side_effect = ZipTooLargeError("Too big!")

        res = _harvest_attempt(pub, ["http://fake.com/huge.zip"])
        assert res == "skip:too_large"
