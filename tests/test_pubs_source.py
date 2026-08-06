"""Pubs Postgres reader (hermetic; no DB) — the DuckDB connection must always be closed.

Each `_from_postgres` call ATTACHes Cloud SQL, and a harvest makes two of them (pubs +
attachments), so a leaked handle holds a server-side connection for the life of the process.
"""
import sys
import types

import pytest

from ugs_warehouse.pubs import source


class _FakeCon:
    def __init__(self):
        self.closed = False
        self.raise_on_select = False

    def execute(self, sql):
        if sql.startswith("DESCRIBE"):
            return types.SimpleNamespace(fetchall=lambda: [("series_id",), ("pub_name",)])
        if sql.startswith("SELECT"):
            if self.raise_on_select:
                raise RuntimeError("relation does not exist")
            return types.SimpleNamespace(fetchall=lambda: [("DS-8", None), ("OFR-1", ["a", "b"])])
        return types.SimpleNamespace(fetchall=list)   # INSTALL / LOAD / ATTACH

    def close(self):
        self.closed = True


@pytest.fixture
def fake_duckdb(monkeypatch):
    con = _FakeCon()
    monkeypatch.setattr(source, "PUBS_DB_URL", "postgresql://u:p@h/db", raising=False)
    monkeypatch.setitem(sys.modules, "duckdb", types.SimpleNamespace(connect=lambda: con))
    return con


def test_from_postgres_closes_and_coerces_rows(fake_duckdb):
    rows = source._from_postgres("pubs.publications")
    assert fake_duckdb.closed
    # None -> "", list -> joined string, so downstream .strip()/.get() behave like the CSV reader.
    assert rows == [{"series_id": "DS-8", "pub_name": ""},
                    {"series_id": "OFR-1", "pub_name": "a, b"}]


def test_from_postgres_closes_when_the_query_raises(fake_duckdb):
    fake_duckdb.raise_on_select = True
    with pytest.raises(RuntimeError):
        source._from_postgres("pubs.publications")
    assert fake_duckdb.closed   # the leak that mattered — the failure path


def test_duckdb_close_actually_tears_the_connection_down():
    """The tests above assert we CALL close(); the fix's value depends on close() doing something.

    Pins that assumption against the real library — if DuckDB ever made close() a no-op, the two
    tests above would still pass while the connections (and their ATTACHed Cloud SQL handles)
    leaked exactly as before. Can't go further without a live Postgres: whether the ATTACH is
    released server-side is only observable from the database's own session list.
    """
    import duckdb

    con = duckdb.connect()
    con.execute("SELECT 1")
    con.close()
    with pytest.raises(duckdb.ConnectionException):
        con.execute("SELECT 1")
