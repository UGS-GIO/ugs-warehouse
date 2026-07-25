"""`read_metadata()` registry query (hermetic; no DB).

Regression cover for the bug where the query ordered by a `status` column that does not exist
on `raw.schema_registry` — Postgres errored, the bare `except` swallowed it, and every topic
silently fell back to `prettify()` for six weeks.
"""
import pytest

from ugs_warehouse.vector import source
from ugs_warehouse.vector.topics import Topic

# Every column raw.schema_registry actually has (prod, seamlessgeolmap, 2026-07-25).
REGISTRY_COLUMNS = {
    "domain_topic", "business_schema", "primary_key", "composite_key_columns",
    "version_mappings", "evolution_history", "display_name", "description", "target_schema",
    "created_at", "updated_at", "created_by", "updated_by", "parquet_exclude_columns",
    "relationships", "unique_constraints", "keywords", "iso_topic_category",
    "use_constraints", "lineage", "point_of_contact",
}


class _CapturingCon:
    """Captures the SQL handed to postgres_query and returns a canned row."""

    def __init__(self, row=None, raises=None):
        self.sql = None
        self._row = row
        self._raises = raises
        self.closed = False

    def execute(self, _query, params):
        self.sql = params[1]
        if self._raises is not None:
            raise self._raises
        return self

    def fetchone(self):
        return self._row

    def close(self):
        self.closed = True


@pytest.fixture
def topic():
    return Topic(schema="hazards", layer="hazards_qfaults_current")


def _run(monkeypatch, topic, con):
    monkeypatch.setattr(source, "_connect", lambda: con)
    return source.read_metadata(topic)


def test_query_references_only_real_registry_columns(monkeypatch, topic):
    con = _CapturingCon(row=None)
    _run(monkeypatch, topic, con)

    # The original bug: `ORDER BY (status = 'active') DESC`. `status` is not a column on
    # raw.schema_registry, so Postgres refused the whole statement.
    assert "status" not in con.sql
    assert "ORDER BY" not in con.sql.upper()

    # Nothing else may drift onto a column the table does not have either.
    selected = con.sql.split(" FROM ")[0].removeprefix("SELECT ").split(", ")
    assert set(selected) <= REGISTRY_COLUMNS
    assert set(selected) == set(source._META_COLS)


def test_query_is_pinned_to_the_topic_stem(monkeypatch, topic):
    con = _CapturingCon(row=None)
    _run(monkeypatch, topic, con)
    # domain_topic is the primary key, which is why no ordering or LIMIT is needed.
    assert "WHERE domain_topic = 'hazards_qfaults'" in con.sql


def test_returns_populated_values_and_drops_empty_ones(monkeypatch, topic):
    # Order matches _META_COLS: display_name, description, keywords, iso_topic_category,
    # use_constraints, lineage, point_of_contact.
    con = _CapturingCon(row=("Quaternary Faults", "", [], None, None, None, "Utah Geological Survey"))
    out = _run(monkeypatch, topic, con)
    assert out == {"display_name": "Quaternary Faults",
                   "point_of_contact": "Utah Geological Survey"}


def test_missing_row_is_empty_not_an_error(monkeypatch, topic):
    assert _run(monkeypatch, topic, _CapturingCon(row=None)) == {}


def test_a_failed_read_still_degrades_but_says_so(monkeypatch, topic, capsys):
    # Degrading to defaults is correct — an uncurated topic is normal. Doing it SILENTLY is what
    # made the status-column bug survive: a broken query and an uncurated topic looked identical.
    con = _CapturingCon(raises=RuntimeError('column "status" does not exist'))
    assert _run(monkeypatch, topic, con) == {}

    err = capsys.readouterr().err
    assert "catalog metadata read FAILED" in err
    assert 'column "status" does not exist' in err
    assert con.closed, "connection must be released even when the query fails"
