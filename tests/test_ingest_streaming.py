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
                "SELECT ST_Point(-111.0, 39.0) AS geom, 1::BIGINT AS feature_id, 'a' AS name")
    return con


def test_streaming_path_runs_sinks_once():
    topic = Topic(schema="energy_mineral", layer="enmin_ucrc_wells_current")
    con = _con_with_transformed()
    backend = MagicMock()
    backend.stream_transformed.return_value = (con, "transformed")
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
    # Cloud Run's handler is long-lived: an unclosed connection leaks per Pub/Sub push.
    assert _is_closed(con)


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


# --- the connection the sinks read through is closed when they're done ----------------------

def _is_closed(con) -> bool:
    try:
        con.execute("SELECT 1")
        return False
    except Exception:
        return True


def test_connection_closed_when_a_sink_raises():
    """Per-sink isolation already swallows the failure; the close must not depend on that."""
    topic = Topic(schema="emp", layer="enmin_ucrc_wells_current")
    con = _con_with_transformed()
    backend = MagicMock()
    backend.stream_transformed.return_value = (con, "transformed")
    backend.read_metadata.return_value = {}

    with patch("ugs_warehouse.vector.ingest._backend", return_value=backend), \
         patch("ugs_warehouse.vector.sink_ducklake.write", side_effect=RuntimeError("boom")), \
         patch("ugs_warehouse.vector.sink_archive.write"), \
         patch("ugs_warehouse.vector.sink_pmtiles.build"), \
         patch("ugs_warehouse.vector.sink_stac.write"), \
         patch("ugs_warehouse.vector.related.resolve", return_value={}), \
         patch("ugs_warehouse.core.stac.refresh_catalog"):

        rc = _ingest(topic, dry_run=False, skip_refresh=False)

    assert rc == 1
    assert _is_closed(con)


# --- #54: the fingerprint gates the DATA sinks, never the STAC sink ------------------------

def _run_unchanged(**kwargs):
    """Ingest a topic whose fingerprint matches the published item."""
    topic = Topic(schema="energy_mineral", layer="enmin_ucrc_wells_current")
    backend = MagicMock()
    backend.stream_transformed.return_value = (_con_with_transformed(), "transformed")
    backend.read_metadata.return_value = {"keywords": ["wells"], "display_name": "Curated Title"}

    with patch("ugs_warehouse.vector.ingest._backend", return_value=backend), \
         patch("ugs_warehouse.vector.fingerprint.is_unchanged", return_value=True), \
         patch("ugs_warehouse.vector.sink_ducklake.write") as ducklake, \
         patch("ugs_warehouse.vector.sink_archive.write") as archive, \
         patch("ugs_warehouse.vector.sink_pmtiles.build") as pmtiles, \
         patch("ugs_warehouse.vector.sink_stac.write") as stac_write, \
         patch("ugs_warehouse.vector.related.resolve", return_value={}), \
         patch("ugs_warehouse.core.stac.refresh_catalog") as refresh:

        rc = _ingest(topic, dry_run=False, skip_refresh=False, **kwargs)

    return rc, ducklake, archive, pmtiles, stac_write, refresh, backend


def test_unchanged_topic_still_republishes_stac():
    """A curator edits raw.schema_registry; no row changes. The edit must still reach the catalog.

    Before #54 the fingerprint match skipped every sink including STAC, so curated titles,
    descriptions and keywords never left the registry unless the data happened to change or
    someone passed --force.
    """
    rc, ducklake, archive, pmtiles, stac_write, refresh, backend = _run_unchanged()

    assert rc == 0
    ducklake.assert_not_called()          # expensive sinks stay skipped — that is the point of it
    archive.assert_not_called()
    pmtiles.assert_not_called()
    stac_write.assert_called_once()       # ...but the item is rewritten
    refresh.assert_called_once()
    # The curated metadata is read and handed to the STAC sink, not stale-cached.
    backend.read_metadata.assert_called_once()
    assert stac_write.call_args.kwargs["metadata"]["display_name"] == "Curated Title"


def test_force_runs_every_sink_even_when_unchanged():
    rc, ducklake, archive, pmtiles, stac_write, _refresh, _backend = _run_unchanged(
        skip_unchanged=False)

    assert rc == 0
    ducklake.assert_called_once()
    archive.assert_called_once()
    pmtiles.assert_called_once()
    stac_write.assert_called_once()
