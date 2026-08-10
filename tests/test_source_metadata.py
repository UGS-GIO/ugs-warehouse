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


def _row(values: dict):
    """A registry row as `to_jsonb` returns it — one JSON object, columns keyed by name."""
    return _CapturingCon(row=(json.dumps(values),))


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

    # Nothing else may drift onto a column the table does not have either. The columns live in
    # the inner subquery that `to_jsonb` wraps.
    inner = con.sql.split("SELECT ", 2)[2]
    selected = inner.split(" FROM ")[0].split(", ")
    assert set(selected) <= REGISTRY_COLUMNS
    assert set(selected) == set(source._META_COLS)


def test_postgres_serializes_the_row_rather_than_us_naming_the_jsonb_columns(monkeypatch, topic):
    # Where the type knowledge lives is the whole point. Per-column `::text` casts put a list of
    # which columns are jsonb in this repo, separate from the DDL that decides it — and the
    # columns nobody has curated yet are the ones such a list gets silently wrong (#64 was
    # `keywords`; `point_of_contact` is jsonb too and was never covered by naming columns).
    con = _CapturingCon(row=None)
    _run(monkeypatch, topic, con)
    assert "to_jsonb(m)::text" in con.sql
    for col in source._META_COLS:
        assert f"{col}::text" not in con.sql


def test_query_is_pinned_to_the_topic_stem(monkeypatch, topic):
    con = _CapturingCon(row=None)
    _run(monkeypatch, topic, con)
    # domain_topic is the primary key, which is why no ordering or LIMIT is needed.
    assert "WHERE domain_topic = 'hazards_qfaults'" in con.sql


def test_returns_populated_values_and_drops_empty_ones(monkeypatch, topic):
    con = _row({"display_name": "Quaternary Faults", "description": "", "keywords": [],
                "iso_topic_category": None, "use_constraints": None, "lineage": None,
                "point_of_contact": {"name": "Utah Geological Survey"}})
    out = _run(monkeypatch, topic, con)
    assert out == {"display_name": "Quaternary Faults",
                   "point_of_contact": {"name": "Utah Geological Survey"}}


def test_keywords_are_parsed_into_a_list_not_split_into_characters(monkeypatch, topic):
    """Regression for #64.

    `keywords` is jsonb; the driver hands it back as a JSON *string*. sink_stac did
    `list(md["keywords"])`, which on a string yields one entry per character — 21 published
    items and 21 ISO records ended up carrying per-character keywords, and the whole suite
    was blind to it.
    """
    con = _row({"keywords": ["counties", "boundaries", "utah"]})
    out = _run(monkeypatch, topic, con)

    assert out["keywords"] == ["counties", "boundaries", "utah"]
    # The precise failure mode, asserted directly.
    assert len(out["keywords"]) == 3, "keywords was split into characters"
    assert "[" not in out["keywords"]
    assert '"' not in out["keywords"]


def test_empty_keywords_array_is_dropped_not_kept_as_a_string(monkeypatch, topic):
    # '[]' is truthy as text, so parsing has to happen BEFORE the empty-filter or every
    # uncurated topic would report keywords it does not have. Same for an empty jsonb object,
    # which only `{}` in the filter catches.
    con = _row({"keywords": [], "point_of_contact": {}})
    assert _run(monkeypatch, topic, con) == {}


def test_unparseable_row_raises_rather_than_degrading(monkeypatch, topic):
    # A malformed value is a registry problem worth surfacing. Silently falling back to the
    # character-split, or to dropping keywords, is how #64 stayed invisible for six weeks.
    con = _CapturingCon(row=("{not valid json",))
    with pytest.raises(json.JSONDecodeError):
        _run(monkeypatch, topic, con)


def test_a_jsonb_scalar_where_an_array_belongs_raises(monkeypatch, topic):
    # `to_jsonb` fixes the TYPE, not the SHAPE: `keywords = '"counties"'::jsonb` is a valid
    # document that parses to a str, survives the empty-filter, and `list(...)` downstream turns
    # it into eight one-character keywords — #64's exact output with the parse looking correct.
    con = _row({"keywords": "counties"})
    with pytest.raises(TypeError, match="keywords"):
        _run(monkeypatch, topic, con)


def test_a_proper_keyword_array_still_passes(monkeypatch, topic):
    out = _run(monkeypatch, topic, _row({"keywords": ["counties", "boundaries"]}))
    assert out["keywords"] == ["counties", "boundaries"]


def test_an_empty_keyword_array_is_dropped_not_raised(monkeypatch, topic):
    # `[]` is the right shape and simply uncurated — the empty-filter removes it before the guard.
    assert "keywords" not in _run(monkeypatch, topic, _row({"keywords": []}))


def test_a_text_column_whose_contents_look_like_json_stays_text(monkeypatch, topic):
    # The reason type knowledge belongs in Postgres rather than a heuristic here: "parse anything
    # that looks like JSON" would turn this description into a list. Postgres knows the column is
    # text, so `to_jsonb` leaves it alone.
    con = _row({"description": '["looks", "like", "json"]'})
    assert _run(monkeypatch, topic, con) == {"description": '["looks", "like", "json"]'}


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


# --- discover() degrades per row: one unusable table name must not cost the whole sweep ------

class _RowsCon:
    """Returns canned (schema, table) rows for the discover query."""

    def __init__(self, rows):
        self._rows = rows
        self.closed = False

    def execute(self, _query, _params):
        return self

    def fetchall(self):
        return self._rows

    def close(self):
        self.closed = True


def test_discover_skips_unusable_names_and_keeps_the_rest(monkeypatch, capsys):
    con = _RowsCon([
        ("hazards", "hazards_qfaults_current"),
        ("hazards", 'bad" name_current'),   # rejected by Topic — cannot be interpolated safely
        ("emp", "geothermal_kgra_current"),
    ])
    monkeypatch.setattr(source, "_connect", lambda: con)

    out = source.discover()

    assert [t.fqn for t in out] == ["hazards.hazards_qfaults_current", "emp.geothermal_kgra_current"]
    assert "skipping hazards.bad" in capsys.readouterr().err
    assert con.closed
