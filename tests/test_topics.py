"""Topic parsing + the Pub/Sub payload contract (dataELT #418)."""
import pytest

from ugs_warehouse.vector.topics import MART_SCHEMAS, Topic, from_pubsub


def test_parse_splits_schema_and_layer():
    t = Topic.parse("hazards.hazards_qfaults_current")
    assert t.schema == "hazards"
    assert t.layer == "hazards_qfaults_current"


def test_stem_strips_current_suffix():
    assert Topic.parse("emp.geothermal_kgra_current").stem == "geothermal_kgra"


def test_fqn_roundtrip():
    assert Topic(layer="x_current", schema="emp").fqn == "emp.x_current"


def test_parse_requires_dotted_form():
    with pytest.raises(ValueError):
        Topic.parse("nodot")


def test_from_pubsub_matches_418_payload():
    t = from_pubsub({"schema": "hazards", "topic": "hazards_qfaults_current"})
    assert t.fqn == "hazards.hazards_qfaults_current"


def test_from_pubsub_accepts_layer_alias():
    assert from_pubsub({"schema": "emp", "layer": "x_current"}).layer == "x_current"


def test_from_pubsub_rejects_incomplete_payload():
    with pytest.raises(ValueError):
        from_pubsub({"schema": "emp"})


# --- schema/layer are interpolated into SQL, so the type rejects non-identifiers ------------

@pytest.mark.parametrize("layer", [
    'x_current" UNION SELECT 1 --',   # escapes source.stream_transformed's identifier quoting
    "x$pgq$ UNION SELECT 1",          # escapes the dollar-quote around the postgres_query body
    "x current",                      # bare space — unquoted in source._describe
    "x.y",                            # `pg.hazards.x.y` resolves as a 3-part catalog path
    "1_current",                      # identifiers cannot start with a digit
    "",
    "x" * 64,                         # past PostgreSQL's 63-char identifier limit
])
def test_rejects_non_identifier_layer(layer):
    with pytest.raises(ValueError):
        Topic(layer=layer, schema="hazards")


def test_rejects_non_identifier_schema():
    with pytest.raises(ValueError):
        Topic(layer="x_current", schema='hazards" --')


def test_from_pubsub_rejects_injected_layer():
    """The payload path is the one nothing else guards — the service gates only `schema`."""
    with pytest.raises(ValueError):
        from_pubsub({"schema": "hazards", "topic": 'x" UNION SELECT 1 --'})


def test_from_pubsub_rejects_non_string_layer():
    """ValueError, not TypeError — the service only handles ValueError, and a TypeError would
    escape as a 500 and put Pub/Sub into redelivery."""
    with pytest.raises(ValueError):
        from_pubsub({"schema": "emp", "topic": 123})


# --- artifacts are keyed by `stem`, so the suffix is required to keep stems distinct ---------

def test_rejects_layer_without_the_serving_suffix():
    """`hazards_qfaults` and `hazards_qfaults_current` would share a stem, so ingesting the first
    would overwrite the second's published parquet, PMTiles, STAC item and DuckLake table."""
    with pytest.raises(ValueError):
        Topic(layer="hazards_qfaults", schema="hazards")


def test_rejects_bare_suffix_as_layer():
    """`_current` is a legal identifier but strips to an empty stem — `archive//.parquet`."""
    with pytest.raises(ValueError):
        Topic(layer="_current", schema="emp")


def test_real_topic_names_still_accepted():
    assert Topic(layer="enmin_ucrc_wells_current", schema="emp").layer == "enmin_ucrc_wells_current"
    assert Topic.parse("gengis.gengis_quads_current").stem == "gengis_quads"


def test_skip_gate_membership():
    # The service acks+skips schemas not in MART_SCHEMAS (e.g. gwportal, separate DB).
    assert "gwportal" not in MART_SCHEMAS
    assert {"hazards", "emp", "gengis", "wetlands", "mapping", "geochron", "boreholes"} <= set(MART_SCHEMAS)
