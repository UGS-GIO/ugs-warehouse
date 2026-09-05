"""Rows the source marks private must not reach the published artifacts (dataELT#666, #35)."""
from __future__ import annotations

from ugs_warehouse.vector import source


def test_detects_the_privacy_column_by_presence():
    """Detection is by column name, so a table that gains one is filtered without anyone
    remembering to configure it."""
    for col in ("confidential", "privacystatus", "PrivacyStatus", "visibility"):
        found = source._privacy_predicate([("id", "INTEGER"), (col, "TEXT")])
        assert found is not None, col
        assert found[0] == col

    assert source._privacy_predicate([("id", "INTEGER"), ("notes", "TEXT")]) is None


def test_the_predicate_keeps_public_rows_and_drops_flagged_ones():
    _, keep = source._privacy_predicate([("privacystatus", "TEXT")])
    # The two live cases: wetlands sites say 'Confidential', OGM wells say 'Yes'.
    assert "'confidential'" in keep and "'yes'" in keep
    assert keep.startswith("lower(coalesce(")
    # A NULL flag is publishable — coalesce keeps it out of the excluded set.
    assert "coalesce" in keep


def test_the_predicate_is_anded_into_every_chunk(monkeypatch):
    """Chunked scans partition by ctid; the filter has to apply to each one, not just the first."""
    monkeypatch.setattr(source, "PAGES_PER_CHUNK", 0)   # single-scan path
    one = source._scan_chunks(None, '"s"."t"', "*", keep="keepme")
    assert len(one) == 1 and "WHERE keepme" in one[0]


def test_no_predicate_leaves_the_scan_untouched(monkeypatch):
    monkeypatch.setattr(source, "PAGES_PER_CHUNK", 0)
    assert "WHERE" not in source._scan_chunks(None, '"s"."t"', "*")[0]
