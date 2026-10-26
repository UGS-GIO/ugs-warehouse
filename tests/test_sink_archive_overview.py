"""The archive sink writes generalized overview levels for heavy line/polygon layers, and only them."""
from __future__ import annotations

from pathlib import Path

import pyarrow.parquet as pq
import pytest

from ugs_warehouse.core import gcs, stac
from ugs_warehouse.vector import sink_archive, sink_stac
from ugs_warehouse.vector.topics import Topic

duckdb = pytest.importorskip("duckdb")

TOPIC = Topic("wetlands_nonriverine_current", "wetlands")
PATHS = [sink_archive.config.overview_path(TOPIC.stem, n) for n in (1, 2, 3)]
LEGACY = f"{sink_archive.config.ARCHIVE_PREFIX}/{TOPIC.stem}/{TOPIC.stem}_overview.parquet"
# 200 detailed circles 0.2 deg across, then a dense field of 0.0001 deg specks, all in one 0.001 cell.
HEAVY = ("SELECT i + 1 AS feature_id, 'w' || i AS name, CASE WHEN i < 200 "
         "THEN ST_Buffer(ST_Point(i, 0), 0.2, 500) "
         "ELSE ST_Buffer(ST_Point(300.0002 + (i % 3) * 0.0002, 0.0002), 0.00005, 2) END AS geom "
         "FROM range(209) r(i)")


@pytest.fixture
def uploads(monkeypatch, tmp_path):
    calls = {"upload": {}, "delete": [], "stored": [LEGACY, *PATHS]}

    def upload(local, path, **_):
        kept = tmp_path / path.replace("/", "_")
        kept.write_bytes(Path(local).read_bytes())
        calls["upload"][path] = str(kept)
        return gcs.FileMeta(kept.stat().st_size)

    monkeypatch.setattr(sink_archive.gcs, "upload", upload)
    monkeypatch.setattr(sink_archive.gcs, "list_paths", lambda prefix: calls["stored"])
    monkeypatch.setattr(sink_archive.gcs, "delete_many",
                        lambda paths, **_: calls["delete"].extend(paths))
    monkeypatch.setattr(sink_archive, "OVERVIEW_MIN_VERTICES", 1)
    monkeypatch.setattr(sink_archive, "OVERVIEW_TOLERANCES", (0.001, 0.01, 0.1))
    return calls


def _con(sql: str):
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial;")
    con.execute(f"CREATE VIEW v AS {sql}")
    return con


def _write(sql: str = HEAVY, archive_size: int = 10**9, listed: set[str] = frozenset()) -> dict:
    written = {"data": gcs.FileMeta(archive_size)}
    sink_archive.write_overviews(TOPIC, _con(sql), "v", written, listed)
    return written


def test_each_tolerance_is_at_most_a_pixel_at_its_zoom():
    """The server reads a level only when the request's offset, a pixel in degrees, is at least it."""
    for tolerance, lod in zip(sink_archive.OVERVIEW_TOLERANCES, (11, 9, 7)):
        assert 0.9 * 360 / (256 * 2**lod) < tolerance <= 360 / (256 * 2**lod)


def test_levels_keep_features_in_order_and_thin_specks_to_one_per_cell(uploads):
    written = _write()
    assert set(written) == {"data", "overview_1", "overview_2", "overview_3"}
    assert list(uploads["upload"]) == PATHS
    level = pq.read_table(uploads["upload"][PATHS[0]], columns=["feature_id", "name"])
    ids = level.column("feature_id").to_pylist()
    # every large feature with its attributes, and one of the nine specks
    assert ids[:200] == list(range(1, 201)) and len(ids) == 201 and ids[200] > 200
    assert level.column("name").to_pylist()[:200] == [f"w{i}" for i in range(200)]
    assert LEGACY in uploads["delete"]


def test_each_level_is_smaller_than_the_last(uploads):
    written = _write()
    sizes = [written[f"overview_{n}"].size for n in (1, 2, 3)]
    assert sizes == sorted(sizes, reverse=True) and sizes[0] < 10**9


def test_a_level_not_much_smaller_than_the_last_is_skipped(uploads, monkeypatch):
    monkeypatch.setattr(sink_archive, "OVERVIEW_TOLERANCES", (0.001, 0.00101, 0.1))
    written = _write()
    assert set(written) == {"data", "overview_1", "overview_3"}
    assert PATHS[1] not in uploads["upload"]


def test_a_layer_of_only_specks_keeps_some(uploads):
    _write("SELECT i + 1 AS feature_id, ST_Buffer(ST_Point(i * 0.5, 0), 0.00005, 2) AS geom FROM range(4) r(i)")
    assert uploads["upload"]
    for local in uploads["upload"].values():
        assert pq.read_metadata(local).num_rows == 4   # one per cell


def test_a_channel_narrower_than_the_tolerance_keeps_its_bends(uploads):
    """At the full tolerance its two banks fold into one straight dash."""
    zigzag = "ST_GeomFromText('LINESTRING(0 0, 0.01 0.0006, 0.02 0, 0.03 0.0006, 0.04 0)')"
    _write(f"SELECT 1 AS feature_id, ST_Buffer({zigzag}, 0.00002) AS geom")
    level = uploads["upload"][PATHS[0]]
    bends = _con("SELECT 1").execute(f"SELECT ST_NPoints(geom) FROM '{level}'").fetchone()[0]
    assert bends >= 8   # its bends; the full tolerance leaves a 4-point dash


def test_points_and_light_layers_write_no_overviews(uploads, monkeypatch):
    monkeypatch.setattr(sink_archive, "OVERVIEW_MIN_VERTICES", 10**6)
    for sql in ("SELECT 1 AS feature_id, ST_Point(0, 0) AS geom",
                "SELECT 1 AS feature_id, ST_Buffer(ST_Point(0, 0), 1) AS geom"):
        assert set(_write(sql)) == {"data"}
    assert not uploads["upload"]


def test_only_files_the_published_item_does_not_list_are_removed(uploads):
    """The catalog index refreshes after the run, so a file it lists must still be there."""
    _write(listed={PATHS[0], PATHS[2]})
    assert uploads["delete"] == [LEGACY, PATHS[1]]


def test_a_failed_level_leaves_the_others_and_is_not_listed(uploads, monkeypatch):
    """The old file is of the old archive, so the item must not go on listing it."""
    real = sink_archive.gcs.upload

    def flaky(local, path, **kw):
        if path == PATHS[1]:
            raise OSError("upload failed")
        return real(local, path, **kw)

    monkeypatch.setattr(sink_archive.gcs, "upload", flaky)
    written = {"data": gcs.FileMeta(10**9)}
    with pytest.raises(RuntimeError, match=r"\[2\]"):
        sink_archive.write_overviews(TOPIC, _con(HEAVY), "v", written)
    assert set(written) == {"data", "overview_1", "overview_3"}


def _overview_assets(monkeypatch, file_meta: dict, published: dict, present: set[str]) -> dict:
    monkeypatch.setattr(sink_stac.gcs, "exists", lambda p: p in present)
    monkeypatch.setattr(sink_stac, "_table_columns", lambda c, v: [])
    monkeypatch.setattr(sink_stac.stac, "prior_file_fields", lambda cp, iid: published)
    assets = sink_stac._assets(TOPIC, None, "v", "x.pmtiles", {}, file_meta)
    return {k: a for k, a in assets.items() if k.startswith("overview")}


def test_a_rewritten_archive_lists_only_the_levels_written_with_it(monkeypatch):
    """A level left out stays in storage until the next run, but the item stops listing it."""
    written = {"data": gcs.FileMeta(9), "overview_1": gcs.FileMeta(5), "overview_3": gcs.FileMeta(2)}
    assets = _overview_assets(monkeypatch, written, {"overview_2": {"file:size": 4}}, set(PATHS))
    assert set(assets) == {"overview_1", "overview_3"}


def test_an_unchanged_archive_keeps_the_published_levels(monkeypatch):
    published = {"overview_1": {"file:size": 5}, "overview_2": {"file:size": 4}}
    assets = _overview_assets(monkeypatch, {}, published, {PATHS[0], PATHS[2]})
    assert set(assets) == {"overview_1"}
    assert sink_stac.published_overviews(TOPIC) == {PATHS[0], PATHS[1]}


def test_stac_lists_each_level_with_its_role_and_tolerance(monkeypatch):
    monkeypatch.setattr(sink_stac.gcs, "exists", lambda p: True)
    monkeypatch.setattr(sink_stac, "_table_columns", lambda c, v: [])
    monkeypatch.setattr(sink_stac.stac, "prior_file_fields", lambda cp, iid: {})
    written = {"data": gcs.FileMeta(9), "overview_1": gcs.FileMeta(5), "overview_3": gcs.FileMeta(2)}
    assets = sink_stac._assets(TOPIC, None, "v", "x.pmtiles", {}, written)
    tols = sink_archive.OVERVIEW_TOLERANCES
    assert {k: (a["roles"], a["ugs:simplify_tolerance"]) for k, a in assets.items() if k.startswith("overview")} \
        == {"overview_1": (["ugs:overview"], tols[0]), "overview_3": (["ugs:overview"], tols[2])}
    assert assets["overview_1"]["file:size"] == 5
    index = stac._index_entry({"id": "x", "assets": assets})["assets"]
    assert index["overview_3"]["ugs:simplify_tolerance"] == tols[2]
    assert index["overview_3"]["roles"] == ["ugs:overview"]
