"""Where a vector item's `datetime` comes from (#65).

Items used to be dated from the ingest clock, which put Utah counties and Quaternary faults seven
minutes apart and gave every ISO record a zero-width temporal extent at the run time. dataELT#504
puts `_publication_date` — the date entered on the upload form — on the serving-table enrichment
allowlist, so the data can date itself.

The column arrives table by table as each is republished, so both populations have to work.
"""
from __future__ import annotations

import datetime

import duckdb
import pytest

from ugs_warehouse.vector.sink_stac import _publication_datetime


def _view(select: str) -> tuple[duckdb.DuckDBPyConnection, str]:
    con = duckdb.connect()
    con.execute(f"CREATE TABLE v AS {select}")
    return con, "v"


def test_no_column_dates_from_the_clock():
    # Most serving tables today. Must not raise: an unconditional MAX() would take the item's
    # STAC + ISO record down entirely, which is worse than a wrong-but-present date.
    con, view = _view("SELECT 1 AS id")
    assert _publication_datetime(con, view) is None


def test_a_date_column_becomes_midnight_utc():
    con, view = _view("SELECT DATE '2024-03-05' AS _publication_date")
    assert _publication_datetime(con, view) == "2024-03-05T00:00:00+00:00"


def test_a_naive_timestamp_is_read_as_utc():
    con, view = _view("SELECT TIMESTAMP '2024-03-05 14:30:00' AS _publication_date")
    assert _publication_datetime(con, view) == "2024-03-05T14:30:00+00:00"


def test_text_is_accepted_since_the_dbt_source_declares_no_type():
    con, view = _view("SELECT '2024-03-05' AS _publication_date")
    assert _publication_datetime(con, view) == "2024-03-05T00:00:00+00:00"


def test_the_newest_publication_in_the_table_wins():
    # A serving table holds the current rows from potentially several loads.
    con, view = _view("""
        SELECT * FROM (VALUES (DATE '2019-01-01'), (DATE '2024-03-05'), (DATE '2021-06-30'))
        AS t(_publication_date)
    """)
    assert _publication_datetime(con, view).startswith("2024-03-05")


def test_an_empty_column_dates_from_the_clock():
    con, view = _view("SELECT CAST(NULL AS DATE) AS _publication_date")
    assert _publication_datetime(con, view) is None


def test_rows_with_no_date_do_not_beat_rows_that_have_one():
    con, view = _view("""
        SELECT * FROM (VALUES (DATE '2024-03-05'), (CAST(NULL AS DATE))) AS t(_publication_date)
    """)
    assert _publication_datetime(con, view).startswith("2024-03-05")


def test_junk_text_falls_back_loudly(capsys):
    con, view = _view("SELECT 'not a date' AS _publication_date")
    assert _publication_datetime(con, view) is None
    assert "_publication_date" in capsys.readouterr().err


def test_a_wrong_type_falls_back_loudly(capsys):
    con, view = _view("SELECT 42 AS _publication_date")
    assert _publication_datetime(con, view) is None
    assert "_publication_date" in capsys.readouterr().err


@pytest.mark.parametrize("sql, expected", [
    ("DATE '2024-03-05'", "2024-03-05T00:00:00+00:00"),
    ("TIMESTAMPTZ '2024-03-05 14:30:00+00'", "2024-03-05T14:30:00+00:00"),
])
def test_the_result_is_always_an_offset_aware_iso_string(sql, expected):
    # STAC requires RFC3339; a naive stamp would be a spec violation the validator catches late.
    con, view = _view(f"SELECT {sql} AS _publication_date")
    got = _publication_datetime(con, view)
    assert got == expected
    assert datetime.datetime.fromisoformat(got).tzinfo is not None


def test_no_connection_dates_from_the_clock():
    # `write()` takes con=None when the caller supplies bbox + row_count; probing must not explode.
    assert _publication_datetime(None, "v") is None

@pytest.fixture
def captured_item(monkeypatch):
    """`write()` with everything but the item build stubbed out — yields what build_item got."""
    from ugs_warehouse.vector import sink_stac
    seen: dict = {}
    monkeypatch.setattr(sink_stac.stac, "manual_override", lambda _id: {})
    monkeypatch.setattr(sink_stac.stac, "prior_property", lambda *_a: None)
    monkeypatch.setattr(sink_stac.stac, "prior_file_fields", lambda cp, iid: {})
    monkeypatch.setattr(sink_stac.gcs, "exists", lambda _p: False)
    monkeypatch.setattr(sink_stac.stac, "build_item",
                        lambda **k: seen.update(k) or {"assets": k["assets"]})
    for fn in ("attach_renders", "attach_classification"):
        monkeypatch.setattr(sink_stac.stac, fn, lambda _i: None)
    monkeypatch.setattr(sink_stac.stac, "write_item", lambda _i: "stac/path.json")
    return seen


def _write(con, view):
    from ugs_warehouse.vector import sink_stac
    from ugs_warehouse.vector.topics import Topic
    sink_stac.write(Topic(schema="hazards", layer="hazards_qfaults_current"), con, view,
                    bbox=[-114.0, 37.0, -109.0, 42.0], row_count=1)


def test_the_item_is_dated_from_the_data(captured_item):
    """The wiring, not just the helper: a correct probe nobody calls dates nothing."""
    _write(*_view("SELECT DATE '2024-03-05' AS _publication_date"))
    assert captured_item["datetime_iso"] == "2024-03-05T00:00:00+00:00"


def test_the_item_falls_back_to_the_clock_without_the_column(captured_item):
    _write(*_view("SELECT 1 AS id"))
    # Today's date, because this table has not been republished with the column yet.
    today = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d")
    assert captured_item["datetime_iso"].startswith(today)
