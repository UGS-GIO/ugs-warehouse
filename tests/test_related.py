"""Related aspatial assets — config + no-op + STAC merge (hermetic; no DB/GCS)."""
from ugs_warehouse.vector import related, sink_stac
from ugs_warehouse.vector.topics import Topic


def test_wells_related_config():
    rels = related.RELATED["enmin_ucrc_wells"]
    assert {r["asset"] for r in rels} == {"boxes", "photos", "attachments"}
    assert all(r["key"] == "uwi" for r in rels)


def test_publish_noop_for_unrelated_topic(monkeypatch):
    # A topic with no related tables returns {} and must never open a DB connection.
    monkeypatch.setattr(related.source, "_connect",
                        lambda: (_ for _ in ()).throw(AssertionError("must not connect")))
    assert related.publish(Topic(schema="hazards", layer="hazards_qfaults_current")) == {}


def test_sink_stac_merges_related_assets(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(sink_stac, "_bbox", lambda c, v: [0, 1, 2, 3])
    monkeypatch.setattr(sink_stac, "_row_count", lambda c, v: 5)
    monkeypatch.setattr(sink_stac.stac, "build_item",
                        lambda **k: captured.update(k) or {"assets": k["assets"]})
    monkeypatch.setattr(sink_stac.stac, "attach_renders", lambda i: None)
    monkeypatch.setattr(sink_stac.stac, "attach_iso", lambda i: None)
    monkeypatch.setattr(sink_stac.stac, "write_item", lambda i: "stac/path.json")

    rel = {"boxes": {"href": "h", "type": "application/vnd.apache.parquet",
                     "roles": ["data", "related"], "title": "UCRC core boxes",
                     "ugs:related_key": "uwi"}}
    sink_stac.write(Topic(schema="energy_mineral", layer="enmin_ucrc_wells_current"),
                    None, "v", related_assets=rel)

    assets = captured["assets"]
    assert "boxes" in assets and assets["boxes"]["ugs:related_key"] == "uwi"
    assert "related" in assets["boxes"]["roles"]
    assert {"data", "pmtiles", "ducklake"} <= set(assets)  # base assets still present
