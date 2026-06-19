from __future__ import annotations

import unittest
from unittest.mock import patch, MagicMock
import pyarrow as pa
from ugs_warehouse.vector.topics import Topic
from ugs_warehouse.vector.ingest import _ingest


def test_ingest_chunked_workflow():
    topic = Topic(schema="hazards", layer="hazards_qfaults_current")

    # 1. Mock the backend (a non-streaming backend, e.g. PostgREST → exercises chunked path;
    #    a streaming backend would take the single-DuckDB branch instead).
    mock_backend = MagicMock()
    del mock_backend.stream_transformed
    mock_backend.get_count.return_value = 25000

    # Mock read_chunk to return a table with a geometry column
    # We will return 2 chunks
    chunk_1 = pa.table({
        "geom_wkb": [b"\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00$@\x00\x00\x00\x00\x00\x00$@"],  # Point (10.0, 10.0) in WKB
        "target_epsg": [4326],
        "name": ["Fault 1"]
    })
    chunk_2 = pa.table({
        "geom_wkb": [b"\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00$@\x00\x00\x00\x00\x00\x00$@"],  # Point (10.0, 10.0) in WKB
        "target_epsg": [4326],
        "name": ["Fault 2"]
    })

    # Keyset streaming: backend yields chunks from a generator (no offset args).
    mock_backend.iter_chunks.return_value = iter([chunk_1, chunk_2])
    mock_backend.read_metadata.return_value = {}

    with patch("ugs_warehouse.vector.ingest._backend", return_value=mock_backend), \
         patch("ugs_warehouse.vector.sink_ducklake.write") as mock_ducklake, \
         patch("ugs_warehouse.core.gcs.upload") as mock_upload, \
         patch("subprocess.run"), \
         patch("shutil.which", return_value=True), \
         patch("ugs_warehouse.vector.sink_stac.write") as mock_stac, \
         patch("ugs_warehouse.core.stac.refresh_catalog") as mock_refresh, \
         patch.dict("os.environ", {"INGEST_CHUNK_SIZE": "15000"}):

         rc = _ingest(topic, dry_run=False, skip_refresh=False)

         assert rc == 0
         # Verified it gets total rows from get_count
         mock_backend.get_count.assert_called_once_with(topic)
         # Verified it streams via keyset iter_chunks (one call, no offset math)
         mock_backend.iter_chunks.assert_called_once_with(topic, 15000)

         # Verified it calls ducklake write twice (first with append=False, second with append=True)
         assert mock_ducklake.call_count == 2
         mock_ducklake.assert_any_call(topic, unittest.mock.ANY, "transformed", append=False)
         mock_ducklake.assert_any_call(topic, unittest.mock.ANY, "transformed", append=True)

         # Verified it calls upload for archive and pmtiles
         assert mock_upload.call_count >= 2

         # Verified it calls stac write once
         mock_stac.assert_called_once()

         # Verified it calls refresh_catalog
         mock_refresh.assert_called_once()
