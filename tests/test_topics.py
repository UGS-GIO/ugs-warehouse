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


def test_skip_gate_membership():
    # The service acks+skips schemas not in MART_SCHEMAS (e.g. gwportal, separate DB).
    assert "gwportal" not in MART_SCHEMAS
    assert {"hazards", "emp", "gengis", "wetlands", "mapping", "geochron", "boreholes"} <= set(MART_SCHEMAS)
