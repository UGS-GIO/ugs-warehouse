"""Streaming ingest path: a backend exposing `stream_transformed` takes the single-DuckDB branch
(no chunking, no pyarrow) and runs the sinks once over the materialized table."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import duckdb

from ugs_warehouse.vector.ingest import _ingest
from ugs_warehouse.vector.topics import Topic


def _con_with_transformed():
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial;")
    con.execute("CREATE TABLE transformed AS "
                "SELECT ST_Point(-111.0, 39.0) AS geom, 1::UBIGINT AS h3_r9, 'a' AS name")
    return con


def test_streaming_path_runs_sinks_once():
    topic = Topic(schema="energy_mineral", layer="enmin_ucrc_wells_current")
    backend = MagicMock()
    backend.stream_transformed.return_value = (_con_with_transformed(), "transformed")
    backend.read_metadata.return_value = {}

    with patch("ugs_warehouse.vector.ingest._backend", return_value=backend), \
         patch("ugs_warehouse.vector.sink_ducklake.write") as ducklake, \
         patch("ugs_warehouse.vector.sink_archive.write") as archive, \
         patch("ugs_warehouse.vector.sink_pmtiles.build") as pmtiles, \
         patch("ugs_warehouse.vector.sink_stac.write") as stac_write, \
         patch("ugs_warehouse.vector.related.resolve", return_value={}), \
         patch("ugs_warehouse.core.stac.refresh_catalog") as refresh:

        rc = _ingest(topic, dry_run=False, skip_refresh=False)

    assert rc == 0
    backend.stream_transformed.assert_called_once_with(topic)
    backend.iter_chunks.assert_not_called()      # streaming, not chunked
    ducklake.assert_called_once()
    archive.assert_called_once()
    pmtiles.assert_called_once()
    stac_write.assert_called_once()
    refresh.assert_called_once()


def test_streaming_dry_run_skips_sinks():
    topic = Topic(schema="energy_mineral", layer="enmin_ucrc_wells_current")
    backend = MagicMock()
    backend.stream_transformed.return_value = (_con_with_transformed(), "transformed")

    with patch("ugs_warehouse.vector.ingest._backend", return_value=backend), \
         patch("ugs_warehouse.vector.sink_archive.write") as archive, \
         patch("ugs_warehouse.core.stac.refresh_catalog") as refresh:

        rc = _ingest(topic, dry_run=True, skip_refresh=False)

    assert rc == 0
    archive.assert_not_called()
    refresh.assert_not_called()
