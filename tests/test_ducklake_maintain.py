"""DuckLake maintenance: compaction actually drains a small-file backlog.

Both assertions here encode a behaviour that was verified against DuckDB 1.5.3 + DuckLake
e6a3bd0a and that the previous implementation got wrong:

  * `ducklake_merge_adjacent_files` compacts one batch per call, so it has to be looped — a single
    call leaves the backlog in place while returning a row per table, which reads like success.
  * Passing `min_file_size`/`max_file_size` turns the merge into a no-op. `compact()` must not pass
    them; this test fails if someone adds them back.
"""
from __future__ import annotations

import glob
import os
import tempfile

import duckdb
import pytest

from ugs_warehouse.vector import maintain

CATALOG = "w"


@pytest.fixture()
def lake():
    con = duckdb.connect()
    try:
        con.execute("INSTALL ducklake; LOAD ducklake;")
    except duckdb.Error as e:
        pytest.skip(f"ducklake extension unavailable: {e}")
    d = tempfile.mkdtemp(prefix="ducklake-maintain-")
    con.execute(f"ATTACH 'ducklake:{d}/catalog.ducklake' AS {CATALOG} (DATA_PATH '{d}/data')")
    # Inlining would keep small commits in the catalog and never produce the parquet backlog
    # this test is about.
    con.execute(f"CALL ducklake_set_option('{CATALOG}', 'data_inlining_row_limit', 0)")
    con.execute(f"CREATE SCHEMA {CATALOG}.emp")
    yield con, d
    con.close()


def _parquet(root: str) -> list[str]:
    return [p for p in glob.glob(f"{root}/**", recursive=True) if p.endswith(".parquet")]


def _seed(con, n: int = 120) -> None:
    """One tiny commit per row — n commits, n small parquet files."""
    con.execute(f"CREATE TABLE {CATALOG}.emp.wells AS SELECT 0 AS i, repeat('x', 300) AS pad")
    for i in range(1, n):
        con.execute(f"INSERT INTO {CATALOG}.emp.wells SELECT {i}, repeat('x', 300)")


def test_compact_drains_backlog_and_preserves_rows(lake):
    con, d = lake
    _seed(con)
    before = len(_parquet(d))
    assert before >= 100, f"fixture did not produce a backlog ({before} files)"

    processed, created = maintain.compact(con, CATALOG)

    assert processed == before, f"compacted {processed} of {before} files"
    assert created < before
    # The superseded files are still on disk until they are expired + collected.
    con.execute(f"SELECT * FROM ducklake_expire_snapshots('{CATALOG}', older_than => now())").fetchall()
    con.execute(f"SELECT * FROM ducklake_cleanup_old_files('{CATALOG}', cleanup_all => true)").fetchall()

    assert len(_parquet(d)) < before
    assert con.execute(f"SELECT count(*) FROM {CATALOG}.emp.wells").fetchone()[0] == 120


def test_compact_respects_budget(lake):
    con, d = lake
    _seed(con)
    # A zero budget must stop before any merge, so the caller still reaches cleanup.
    processed, created = maintain.compact(con, CATALOG, budget_seconds=0)
    assert (processed, created) == (0, 0)
    assert len(_parquet(d)) >= 100


def _target_bytes(con) -> int | None:
    opts = dict(con.execute(f"SELECT option_name, value FROM ducklake_options('{CATALOG}')").fetchall())
    return maintain._as_bytes(opts.get("target_file_size"))


def test_ensure_options_pins_target_file_size(lake, capsys):
    con, _ = lake
    maintain.ensure_options(con, CATALOG, target_file_size="256MB")
    # DuckLake normalizes the suffix away on write ('256MB' reads back as '256000000').
    assert _target_bytes(con) == 256_000_000

    # Idempotent, and it must recognise its own normalized value rather than rewriting it
    # on every run.
    capsys.readouterr()
    maintain.ensure_options(con, CATALOG, target_file_size="256MB")
    assert "already" in capsys.readouterr().out
    assert _target_bytes(con) == 256_000_000


def test_ensure_options_dry_run_changes_nothing(lake):
    con, _ = lake
    maintain.ensure_options(con, CATALOG, target_file_size="256MB", dry_run=True)
    opts = dict(con.execute(f"SELECT option_name, value FROM ducklake_options('{CATALOG}')").fetchall())
    assert "target_file_size" not in opts


def test_report_is_read_only(lake, capsys):
    con, d = lake
    _seed(con, n=20)
    before = sorted(_parquet(d))
    assert maintain.report(con, CATALOG) == 0
    assert sorted(_parquet(d)) == before
    assert "emp.wells" in capsys.readouterr().out


def test_orphaned_file_is_collected(lake):
    """A parquet the catalog never referenced — what a died-mid-ingest run leaves behind."""
    con, d = lake
    _seed(con, n=5)
    real = _parquet(d)[0]
    orphan = os.path.join(os.path.dirname(real), "ducklake-orphan-test.parquet")
    with open(real, "rb") as src, open(orphan, "wb") as dst:
        dst.write(src.read())

    # cleanup_old_files walks catalog references only, so it cannot see this file.
    con.execute(f"SELECT * FROM ducklake_cleanup_old_files('{CATALOG}', cleanup_all => true)").fetchall()
    assert os.path.exists(orphan)

    found = con.execute(
        f"SELECT * FROM ducklake_delete_orphaned_files('{CATALOG}', cleanup_all => true, dry_run => true)"
    ).fetchall()
    assert [orphan] == [r[0] for r in found]
