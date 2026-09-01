"""source._scan_chunks: partition the Postgres read into ctid page ranges.

The hydrate OOMed on huc12 because the whole table's geometry had to land at once. These pin that
the partition is disjoint, covers rows written past the size snapshot, and degrades to a single
scan rather than failing when it cannot size the table.
"""
from __future__ import annotations

import re

import duckdb
import pytest

from ugs_warehouse.vector import source

REL = '"mapping"."huc12_current"'
SELECT_LIST = '"id", ST_AsBinary("geom") AS geom_wkb, ST_SRID("geom") AS target_epsg'


class _Con:
    """Stands in for the DuckDB connection: answers the size probe, or raises like a failed one."""

    def __init__(self, pages: int | None):
        self.pages = pages

    def execute(self, sql: str):
        if self.pages is None:
            raise duckdb.Error("no such table")
        return _Result(self.pages)


class _Result:
    def __init__(self, pages: int):
        self.pages = pages

    def fetchone(self):
        return (self.pages,)


def _ranges(chunks: list[str]) -> list[tuple[int, int | None]]:
    """(lo, hi) page bounds parsed back out of each chunk's WHERE clause."""
    out = []
    for c in chunks:
        lo = re.search(r"ctid >= '\((\d+),0\)'", c)
        hi = re.search(r"ctid < '\((\d+),0\)'", c)
        out.append((int(lo.group(1)) if lo else 0, int(hi.group(1)) if hi else None))
    return out


def test_small_table_is_one_scan(monkeypatch):
    monkeypatch.setattr(source, "PAGES_PER_CHUNK", 2000)
    chunks = source._scan_chunks(_Con(pages=10), REL, SELECT_LIST)
    assert len(chunks) == 1
    assert "ctid" not in chunks[0]


def test_large_table_is_partitioned_disjointly(monkeypatch):
    monkeypatch.setattr(source, "PAGES_PER_CHUNK", 1000)
    chunks = source._scan_chunks(_Con(pages=5000), REL, SELECT_LIST)
    ranges = _ranges(chunks)

    # Every chunk starts where the previous ended: no gaps (dropped rows), no overlaps (dupes).
    bounded = [r for r in ranges if r[1] is not None]
    assert bounded[0][0] == 0
    for (_, prev_hi), (lo, _) in zip(bounded, bounded[1:]):
        assert lo == prev_hi


def test_tail_chunk_is_open_ended(monkeypatch):
    """pg_relation_size is a snapshot; anything written past it must still be read."""
    monkeypatch.setattr(source, "PAGES_PER_CHUNK", 1000)
    ranges = _ranges(source._scan_chunks(_Con(pages=5000), REL, SELECT_LIST))
    assert ranges[-1][1] is None
    assert ranges[-1][0] >= 5000


def test_falls_back_to_one_scan_when_sizing_fails(monkeypatch):
    monkeypatch.setattr(source, "PAGES_PER_CHUNK", 1000)
    chunks = source._scan_chunks(_Con(pages=None), REL, SELECT_LIST)
    assert len(chunks) == 1
    assert "ctid" not in chunks[0]


def test_chunking_can_be_disabled(monkeypatch):
    monkeypatch.setattr(source, "PAGES_PER_CHUNK", 0)
    chunks = source._scan_chunks(_Con(pages=99999), REL, SELECT_LIST)
    assert len(chunks) == 1
    assert "ctid" not in chunks[0]


@pytest.mark.parametrize("pages", [1001, 2000, 2001])
def test_every_chunk_carries_the_select_list(monkeypatch, pages):
    monkeypatch.setattr(source, "PAGES_PER_CHUNK", 1000)
    for chunk in source._scan_chunks(_Con(pages=pages), REL, SELECT_LIST):
        assert SELECT_LIST in chunk
        assert REL in chunk
