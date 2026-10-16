"""The archive sink writes a generalized overview for heavy line/polygon layers, and only them."""
from __future__ import annotations

from pathlib import Path

import pyarrow.parquet as pq
import pytest

from ugs_warehouse.vector import sink_archive
from ugs_warehouse.vector.topics import Topic

duckdb = pytest.importorskip("duckdb")


@pytest.fixture
def uploads(monkeypatch, tmp_path):
    calls = {"upload": [], "delete": []}

    def upload(local, path, **_):
        kept = tmp_path / path.replace("/", "_")
        kept.write_bytes(Path(local).read_bytes())
        calls["upload"].append((path, str(kept)))

    monkeypatch.setattr(sink_archive.gcs, "upload", upload)
    monkeypatch.setattr(sink_archive.gcs, "delete", lambda path: calls["delete"].append(path))
    return calls


def _con(sql: str):
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial;")
    con.execute(f"CREATE VIEW v AS {sql}")
    return con


TOPIC = Topic("wetlands_nonriverine_current", "wetlands")


def test_heavy_polygons_get_an_overview_that_keeps_feature_ids(uploads, monkeypatch):
    monkeypatch.setattr(sink_archive, "OVERVIEW_MIN_BYTES", 1)
    # Detailed 2000-vertex circles plus specks smaller than the tolerance, which keep an envelope.
    con = _con("SELECT i + 1 AS feature_id, ST_Buffer(ST_Point(i, 0), CASE WHEN i % 2 = 0 THEN 0.2 ELSE 0.0001 END, 500) "
               "AS geom FROM range(20) r(i)")
    sink_archive.write_overview(TOPIC, con, "v")
    [(path, local)] = uploads["upload"]
    assert path.endswith("wetlands_nonriverine/wetlands_nonriverine_overview.parquet")
    table = pq.read_table(local, columns=["feature_id"])
    assert table.column("feature_id").to_pylist() == list(range(1, 21))   # every feature, ids kept
    assert pq.read_metadata(local).num_rows == 20


def test_points_and_light_layers_remove_a_stale_overview(uploads):
    for sql in ("SELECT 1 AS feature_id, ST_Point(0, 0) AS geom",
                "SELECT 1 AS feature_id, ST_Buffer(ST_Point(0, 0), 1) AS geom"):
        sink_archive.write_overview(TOPIC, _con(sql), "v")
    assert not uploads["upload"]
    assert len(uploads["delete"]) == 2


def test_a_failed_overview_removes_the_stale_one(uploads, monkeypatch):
    """Otherwise the item would go on listing the old overview beside the new archive."""
    monkeypatch.setattr(sink_archive, "OVERVIEW_MIN_BYTES", 1)

    def fail(*_, **__):
        raise OSError("upload failed")

    monkeypatch.setattr(sink_archive.gcs, "upload", fail)
    with pytest.raises(OSError):
        con = _con("SELECT 1 AS feature_id, ST_Buffer(ST_Point(0, 0), 1) AS geom")
        sink_archive.write_overview(TOPIC, con, "v")
    assert uploads["delete"] == [sink_archive.config.overview_path(TOPIC.stem)]
