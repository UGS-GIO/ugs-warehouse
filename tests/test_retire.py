"""Retiring a topic: the DuckLake drop that `drop_dangling_tables` cannot do, and the artifact
sweep that follows it.

Two behaviours are worth pinning:

  * `drop_table` reads the table's files BEFORE dropping it. A dropped table cannot be listed, so
    an implementation that drops first strands every file it was supposed to collect.
  * retire deletes by `<stem>/` prefix. Without the trailing slash, retiring `hazards_qfaults`
    would take `hazards_qfaults_zones` with it.
"""
from __future__ import annotations

import glob
import tempfile

import duckdb
import pytest

from ugs_warehouse.core import config
from ugs_warehouse.vector import maintain, retire
from ugs_warehouse.vector.sink_stac import CATALOG
from ugs_warehouse.vector.topics import Topic

LAKE = "w"
TOPIC = Topic(layer="hazards_qfaults_current", schema="hazards")


@pytest.fixture()
def lake():
    con = duckdb.connect()
    try:
        con.execute("INSTALL ducklake; LOAD ducklake;")
    except duckdb.Error as e:
        pytest.skip(f"ducklake extension unavailable: {e}")
    d = tempfile.mkdtemp(prefix="ducklake-retire-")
    con.execute(f"ATTACH 'ducklake:{d}/catalog.ducklake' AS {LAKE} (DATA_PATH '{d}/data')")
    con.execute(f"CALL ducklake_set_option('{LAKE}', 'data_inlining_row_limit', 0)")
    con.execute(f"CREATE SCHEMA {LAKE}.hazards")
    yield con, d
    con.close()


def _parquet(root: str) -> list[str]:
    return [p for p in glob.glob(f"{root}/**", recursive=True) if p.endswith(".parquet")]


def _seed(con) -> None:
    con.execute(f"CREATE TABLE {LAKE}.hazards.hazards_qfaults AS SELECT 1 AS i")
    con.execute(f"CREATE TABLE {LAKE}.hazards.hazards_qfaults_zones AS SELECT 2 AS i")


def _tables(con) -> set[str]:
    return {r[0] for r in con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_catalog = ?", [LAKE]
    ).fetchall()}


def test_drop_table_removes_the_table_and_its_parquet(lake, monkeypatch):
    con, d = lake
    monkeypatch.setattr(maintain.ducklake, "DATA_PATH", f"{d}/data")
    _seed(con)
    before = len(_parquet(d))
    assert before == 2, f"fixture wrote {before} file(s)"

    files = maintain.drop_table(con, LAKE, "hazards.hazards_qfaults")

    assert len(files) == 1
    assert _tables(con) == {"hazards_qfaults_zones"}
    # The neighbouring topic keeps its data — this is a per-topic drop, not a schema wipe.
    assert len(_parquet(d)) == 1
    assert con.execute(f"SELECT count(*) FROM {LAKE}.hazards.hazards_qfaults_zones").fetchone()[0] == 1


def test_drop_table_dry_run_reports_the_files_and_keeps_them(lake, monkeypatch):
    con, d = lake
    monkeypatch.setattr(maintain.ducklake, "DATA_PATH", f"{d}/data")
    _seed(con)

    files = maintain.drop_table(con, LAKE, "hazards.hazards_qfaults", dry_run=True)

    assert len(files) == 1
    assert _tables(con) == {"hazards_qfaults", "hazards_qfaults_zones"}
    assert len(_parquet(d)) == 2


def test_drop_table_on_an_absent_table_is_not_an_error(lake):
    con, _ = lake
    assert maintain.drop_table(con, LAKE, "hazards.never_ingested") == []


def _published() -> list[str]:
    stem = TOPIC.stem
    return [
        f"{config.ARCHIVE_PREFIX}/{stem}/{stem}.parquet",
        f"{config.ARCHIVE_PREFIX}/{stem}/{stem}_20260901.parquet",
        f"{config.PMTILES_PREFIX}/{stem}/{stem}.pmtiles",
        f"{config.THUMBS_PREFIX}/{stem}/{stem}.webp",
        f"{config.THUMBS_PREFIX}/{stem}/{stem}.sha",
        f"{config.STAC_PREFIX}/{CATALOG}/{TOPIC.schema}/{stem}/{stem}.json",
        f"{config.STAC_PREFIX}/{CATALOG}/{TOPIC.schema}/{stem}/{stem}.iso.xml",
        # A different topic whose stem starts with ours.
        f"{config.ARCHIVE_PREFIX}/{stem}_zones/{stem}_zones.parquet",
    ]


@pytest.fixture()
def fake_gcs(monkeypatch):
    """In-memory object store + a no-op DuckLake and catalog refresh. Records what was deleted."""
    objects = list(_published())
    objects.append(f"{config.OVERRIDES_PREFIX}/{TOPIC.stem}.json")
    deleted: list[str] = []
    refreshed: list[bool] = []

    monkeypatch.setattr(retire.gcs, "list_paths", lambda pre: [p for p in objects if p.startswith(pre)])
    monkeypatch.setattr(retire.gcs, "exists", lambda p: p in objects)
    monkeypatch.setattr(retire.gcs, "delete", lambda p: (deleted.append(p), objects.remove(p))[0])
    monkeypatch.setattr(retire.stac, "refresh_catalog", lambda: refreshed.append(True))
    monkeypatch.setattr(retire.styles, "entry_for", lambda item_id: None)
    monkeypatch.setattr(retire, "references", lambda stem: [])
    monkeypatch.setattr(retire.ducklake, "attach", lambda con: LAKE)
    monkeypatch.setattr(retire.maintain, "drop_table", lambda *a, **k: [])
    return deleted, refreshed


def test_retire_deletes_every_artifact_and_refreshes_the_catalog(fake_gcs):
    deleted, refreshed = fake_gcs
    stem = TOPIC.stem

    assert retire.retire(TOPIC, assume_yes=True) == 0

    assert set(deleted) == set(_published()[:-1])
    # Prefix match must not reach a longer stem, or its data goes with the retired topic.
    assert f"{config.ARCHIVE_PREFIX}/{stem}_zones/{stem}_zones.parquet" not in deleted
    assert refreshed == [True]


def test_retire_keeps_the_metadata_override_unless_asked(fake_gcs):
    deleted, _ = fake_gcs
    override = f"{config.OVERRIDES_PREFIX}/{TOPIC.stem}.json"

    retire.retire(TOPIC, assume_yes=True)
    assert override not in deleted

    retire.retire(TOPIC, assume_yes=True, purge_overrides=True)
    assert override in deleted


def test_dry_run_deletes_nothing_and_leaves_the_catalog_alone(fake_gcs):
    deleted, refreshed = fake_gcs

    assert retire.retire(TOPIC, dry_run=True) == 0

    assert deleted == []
    assert refreshed == []


def test_refuses_without_the_typed_confirmation(fake_gcs, monkeypatch):
    deleted, refreshed = fake_gcs
    monkeypatch.setattr(retire, "confirmed", lambda topic: False)

    assert retire.retire(TOPIC) == 2
    assert (deleted, refreshed) == ([], [])


def test_confirmation_takes_the_full_topic_name_only(monkeypatch):
    monkeypatch.setattr(retire.sys.stdin, "isatty", lambda: True)

    monkeypatch.setattr("builtins.input", lambda prompt: "hazards_qfaults")
    assert retire.confirmed(TOPIC) is False

    monkeypatch.setattr("builtins.input", lambda prompt: f" {TOPIC.fqn} ")
    assert retire.confirmed(TOPIC) is True


def test_a_dry_run_needs_no_confirmation(fake_gcs, monkeypatch):
    deleted, _ = fake_gcs
    monkeypatch.setattr(retire, "confirmed", lambda topic: pytest.fail("dry-run prompted"))

    assert retire.retire(TOPIC, dry_run=True) == 0
    assert deleted == []
