"""`read_metadata()` registry query (hermetic; no DB).

Regression cover for the bug where the query ordered by a `status` column that does not exist
on `raw.schema_registry` — Postgres errored, the bare `except` swallowed it, and every topic
silently fell back to `prettify()` for six weeks.
"""
import json

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

    # Nothing else may drift onto a column the table does not have either. jsonb columns carry
    # an explicit ::text cast, so compare on the bare column name.
    selected = [c.removesuffix("::text")
                for c in con.sql.split(" FROM ")[0].removeprefix("SELECT ").split(", ")]
    assert set(selected) <= REGISTRY_COLUMNS
    assert set(selected) == set(source._META_COLS)


def test_jsonb_columns_are_cast_to_text_in_the_select(monkeypatch, topic):
    # The cast is what makes parsing possible. Without it the driver returns the raw JSON
    # string anyway, and `list()` on it splits the text into characters — the #64 bug.
    con = _CapturingCon(row=None)
    _run(monkeypatch, topic, con)
    assert "keywords::text" in con.sql
    for col in source._META_COLS:
        if col not in source._JSON_COLS:
            assert f"{col}::text" not in con.sql


def test_query_is_pinned_to_the_topic_stem(monkeypatch, topic):
    con = _CapturingCon(row=None)
    _run(monkeypatch, topic, con)
    # domain_topic is the primary key, which is why no ordering or LIMIT is needed.
    assert "WHERE domain_topic = 'hazards_qfaults'" in con.sql


def test_returns_populated_values_and_drops_empty_ones(monkeypatch, topic):
    # Order matches _META_COLS: display_name, description, keywords, iso_topic_category,
    # use_constraints, lineage, point_of_contact. `keywords` arrives as JSON text because of
    # the ::text cast.
    con = _CapturingCon(
        row=("Quaternary Faults", "", "[]", None, None, None, "Utah Geological Survey"))
    out = _run(monkeypatch, topic, con)
    assert out == {"display_name": "Quaternary Faults",
                   "point_of_contact": "Utah Geological Survey"}


def test_keywords_are_parsed_into_a_list_not_split_into_characters(monkeypatch, topic):
    """Regression for #64.

    `keywords` is jsonb; the driver hands it back as a JSON *string*. sink_stac did
    `list(md["keywords"])`, which on a string yields one entry per character — 21 published
    items and 21 ISO records ended up carrying per-character keywords, and the whole suite
    was blind to it.
    """
    con = _CapturingCon(
        row=(None, None, '["counties", "boundaries", "utah"]', None, None, None, None))
    out = _run(monkeypatch, topic, con)

    assert out["keywords"] == ["counties", "boundaries", "utah"]
    # The precise failure mode, asserted directly.
    assert len(out["keywords"]) == 3, "keywords was split into characters"
    assert "[" not in out["keywords"]
    assert '"' not in out["keywords"]


def test_empty_keywords_array_is_dropped_not_kept_as_a_string(monkeypatch, topic):
    # '[]' is truthy as text, so parsing has to happen BEFORE the empty-filter or every
    # uncurated topic would report keywords it does not have.
    con = _CapturingCon(row=(None, None, "[]", None, None, None, None))
    assert _run(monkeypatch, topic, con) == {}


def test_unparseable_keywords_raises_rather_than_degrading(monkeypatch, topic):
    # A malformed jsonb value is a registry problem worth surfacing. Silently falling back to
    # the character-split, or to dropping keywords, is how #64 stayed invisible for six weeks.
    con = _CapturingCon(row=(None, None, "{not valid json", None, None, None, None))
    with pytest.raises(json.JSONDecodeError):
        _run(monkeypatch, topic, con)


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
