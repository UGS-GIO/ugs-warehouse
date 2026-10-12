"""Relationships → STAC related links + Frictionless foreignKeys + Table ext (hermetic; no DB/GCS).

These cover the pure projection helpers and the sink_stac wiring. The registry round-trip
(resolve()) is exercised by faking `_pg` so no Postgres/GCS is touched.
"""
from ugs_warehouse.core import config
from ugs_warehouse.vector import related, sink_stac
from ugs_warehouse.vector.topics import Topic


class _FakeCon:
    def execute(self, *a, **k):
        raise AssertionError("resolve() must not run real SQL in tests")

    def close(self):
        pass


def test_foreign_key_single_and_composite():
    single = related._foreign_key(
        {"sourceColumn": "uwi", "targetDomainTopic": "enmin_ucrc_wells", "targetColumn": "uwi"})
    assert single == {"fields": ["uwi"],
                      "reference": {"resource": "enmin_ucrc_wells",
                                    "href": config.public_url(config.archive_path("enmin_ucrc_wells")),
                                    "fields": ["uwi"]}}

    comp = related._foreign_key(
        {"sourceColumns": ["quad", "year"], "targetDomainTopic": "mapping_quads_24k",
         "targetColumns": ["quad_id", "yr"]})
    assert comp == {"fields": ["quad", "year"],
                    "reference": {"resource": "mapping_quads_24k",
                                  "href": config.public_url(config.archive_path("mapping_quads_24k")),
                                  "fields": ["quad_id", "yr"]}}

    assert related._foreign_key({"targetDomainTopic": "x"}) is None  # no columns → no FK


def test_has_geometry_and_table_columns():
    spatial = {"id": {"type": "INTEGER"}, "geom": {"type": "geometry(MultiPolygon,4326)"}}
    aspatial = {"uwi": {"type": "TEXT"}, "depth": {"type": "DOUBLE", "description": "ft"}}
    assert related._has_geometry(spatial) is True
    assert related._has_geometry(aspatial) is False

    cols = related._table_columns(aspatial)
    assert {"name": "uwi", "type": "TEXT"} in cols
    assert {"name": "depth", "type": "DOUBLE", "description": "ft"} in cols


def test_resolve_emits_links_fks_and_aspatial_assets(monkeypatch):
    # Parent "enmin_ucrc_wells": one OUTGOING FK (→ mapping_quads_24k), one aspatial child
    # (enmin_ucrc_boxes, no geometry → materialised), one spatial child (link only).
    monkeypatch.setattr(related.source, "_connect", lambda: _FakeCon())
    monkeypatch.setattr(related, "_materialize_child",
                        lambda con, child, schema, disp, rel, bs, parent: {
                            "href": "h", "type": related.PARQUET_MIME,
                            "roles": ["data", "related"], "title": disp,
                            "ugs:foreign_keys": [related._foreign_key(rel)]})

    def fake_pg(con, sql):
        if "domain_topic =" in sql:  # outgoing FKs of the parent
            return [('[{"sourceColumn": "quad", "targetDomainTopic": "mapping_quads_24k", '
                     '"targetColumn": "quad_id"}]',)]
        if "domain_topic IN" in sql:  # FK targets' schema + display + business_schema
            return [("mapping_quads_24k", "mapping", "24k Quads",
                     '{"quad_id": {"type": "TEXT"}, "geom": {"type": "geometry(Polygon,4326)"}}')]
        # incoming children (containment query): one aspatial, one spatial
        return [
            ("enmin_ucrc_boxes", "energy_mineral", "UCRC core boxes",
             '{"uwi": {"type": "TEXT"}}',
             '[{"sourceColumn": "uwi", "targetDomainTopic": "enmin_ucrc_wells", '
             '"targetColumn": "uwi"}]'),
            ("enmin_ucrc_sites", "energy_mineral", "UCRC sites",
             '{"geom": {"type": "geometry(Point,4326)"}}',
             '[{"sourceColumn": "uwi", "targetDomainTopic": "enmin_ucrc_wells", '
             '"targetColumn": "uwi"}]'),
        ]
    monkeypatch.setattr(related, "_pg", fake_pg)

    out = related.resolve(Topic(schema="energy_mineral", layer="enmin_ucrc_wells_current"))

    # Outgoing FK → foreignKeys on the parent + a related link to the target.
    # reference.href resolves the join: `resource` alone is a bare topic name.
    assert {"fields": ["quad"],
            "reference": {"resource": "mapping_quads_24k",
                          "href": config.public_url(config.archive_path("mapping_quads_24k")),
                          "fields": ["quad_id"]}} in out["foreign_keys"]
    hrefs = [lk["href"] for lk in out["links"]]
    assert any("mapping_quads_24k" in h for h in hrefs)        # outgoing target link
    assert any("enmin_ucrc_sites" in h for h in hrefs)         # spatial child link
    assert all(lk["rel"] == "related" for lk in out["links"])
    # Aspatial child materialised as an asset; spatial child is NOT an asset (it has its own item).
    assert "enmin_ucrc_boxes" in out["assets"]
    assert "enmin_ucrc_sites" not in out["assets"]
    assert out["assets"]["enmin_ucrc_boxes"]["roles"] == ["data", "related"]


def test_resolve_materialises_an_aspatial_outgoing_target(monkeypatch):
    # An OUTGOING FK to an aspatial lookup (wetlands_plants_site → wetlands_plants_projects). The
    # target has no STAC item of its own, so it must be materialised as a related-table asset (like an
    # aspatial child), NOT emitted as a dangling geo+json link (warehouse#347). Its FK is flipped to
    # the asset's own orientation (target.projectcode → parent.project) for the viewer's click-join.
    monkeypatch.setattr(related.source, "_connect", lambda: _FakeCon())
    monkeypatch.setattr(related, "_materialize_child",
                        lambda con, topic, schema, disp, rel, bs, parent: {
                            "href": "h", "type": related.PARQUET_MIME,
                            "roles": ["data", "related"], "title": disp,
                            "ugs:foreign_keys": [related._foreign_key(rel)]})

    def fake_pg(con, sql):
        if "domain_topic =" in sql:  # outgoing FKs of the parent
            return [('[{"sourceColumn": "project", "targetDomainTopic": "wetlands_plants_projects", '
                     '"targetColumn": "projectcode"}]',)]
        if "domain_topic IN" in sql:  # target meta: schema, display, aspatial business_schema
            return [("wetlands_plants_projects", "wetlands", "Wetlands Plants Projects",
                     '{"projectcode": {"type": "TEXT"}}')]
        return []  # no incoming children
    monkeypatch.setattr(related, "_pg", fake_pg)

    out = related.resolve(Topic(schema="wetlands", layer="wetlands_plants_site_current"))

    # Materialised as a related asset; NOT a link (the item does not exist → the link would dangle).
    assert "wetlands_plants_projects" in out["assets"]
    assert out["assets"]["wetlands_plants_projects"]["roles"] == ["data", "related"]
    assert not any("wetlands_plants_projects" in lk["href"] for lk in out["links"])
    # The FK on the parent's own data asset stays in the declared (parent → target) orientation.
    assert {"fields": ["project"],
            "reference": {"resource": "wetlands_plants_projects",
                          "href": config.public_url(config.archive_path("wetlands_plants_projects")),
                          "fields": ["projectcode"]}} in out["foreign_keys"]
    # The materialised asset's own FK is flipped to (target → parent) so the click-join filters
    # the lookup by the clicked parent row.
    assert out["assets"]["wetlands_plants_projects"]["ugs:foreign_keys"] == [{
        "fields": ["projectcode"],
        "reference": {"resource": "wetlands_plants_site",
                      "href": config.public_url(config.archive_path("wetlands_plants_site")),
                      "fields": ["project"]}}]


def test_resolve_links_an_outgoing_target_with_no_business_schema(monkeypatch):
    # A spatial outgoing target can have a target_schema but no business_schema row in the registry.
    # It must still LINK to its item, never be materialised: materialising on an unconfirmed schema
    # would COPY a possibly-spatial table and drop the valid link. Only a CONFIRMED geometry-less
    # target is materialised.
    monkeypatch.setattr(related.source, "_connect", lambda: _FakeCon())

    def _no_materialise(*a, **k):
        raise AssertionError("must not materialise a target whose geometry is unconfirmed")
    monkeypatch.setattr(related, "_materialize_child", _no_materialise)

    def fake_pg(con, sql):
        if "domain_topic =" in sql:
            return [('[{"sourceColumn": "quad", "targetDomainTopic": "mapping_quads_24k", '
                     '"targetColumn": "quad_id"}]',)]
        if "domain_topic IN" in sql:  # has a target_schema but NULL business_schema
            return [("mapping_quads_24k", "mapping", "24k Quads", None)]
        return []
    monkeypatch.setattr(related, "_pg", fake_pg)

    out = related.resolve(Topic(schema="hazards", layer="hazards_qfaults_current"))
    assert any("mapping_quads_24k" in lk["href"] for lk in out["links"])
    assert out["assets"] == {}


def test_resolve_materialises_a_repeated_outgoing_target_once(monkeypatch):
    # Two outgoing FKs to the SAME aspatial lookup: materialise it once and keep BOTH flipped FKs on
    # the single asset. A re-COPY would repeat the DB read + GCS upload, and an overwrite would drop
    # every FK but the last.
    monkeypatch.setattr(related.source, "_connect", lambda: _FakeCon())
    calls = {"n": 0}

    def fake_materialise(con, topic, schema, disp, rel, bs, parent):
        calls["n"] += 1
        return {"href": "h", "type": related.PARQUET_MIME, "roles": ["data", "related"],
                "title": disp, "ugs:foreign_keys": [related._foreign_key(rel)]}
    monkeypatch.setattr(related, "_materialize_child", fake_materialise)

    def fake_pg(con, sql):
        if "domain_topic =" in sql:  # two outgoing FKs, same target, different columns
            return [('[{"sourceColumn": "project", "targetDomainTopic": "wetlands_plants_projects", '
                     '"targetColumn": "projectcode"}, '
                     '{"sourceColumn": "altproject", "targetDomainTopic": "wetlands_plants_projects", '
                     '"targetColumn": "altcode"}]',)]
        if "domain_topic IN" in sql:
            return [("wetlands_plants_projects", "wetlands", "Wetlands Projects Dataset",
                     '{"projectcode": {"type": "TEXT"}}')]
        return []
    monkeypatch.setattr(related, "_pg", fake_pg)

    out = related.resolve(Topic(schema="wetlands", layer="wetlands_plants_site_current"))
    assert calls["n"] == 1  # materialised once, not once per relationship
    fks = out["assets"]["wetlands_plants_projects"]["ugs:foreign_keys"]
    assert len(fks) == 2
    assert [f["fields"] for f in fks] == [["projectcode"], ["altcode"]]
    assert all(f["reference"]["resource"] == "wetlands_plants_site" for f in fks)


def test_resolve_graceful_on_db_error(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no grant on raw.schema_registry")
    monkeypatch.setattr(related.source, "_connect", lambda: _FakeCon())
    monkeypatch.setattr(related, "_pg", boom)
    out = related.resolve(Topic(schema="hazards", layer="hazards_qfaults_current"))
    assert out == {"assets": {}, "links": [], "foreign_keys": []}


def test_sink_stac_links_the_topics_features_collection(monkeypatch):
    """featureserv names its collections after the STAC item id, so the queryable endpoint is
    addressable on the item and nowhere else — the collection doc can only link the service root."""
    captured: dict = {}
    monkeypatch.setattr(sink_stac, "_bbox", lambda c, v: [0, 1, 2, 3])
    monkeypatch.setattr(sink_stac, "_row_count", lambda c, v: 5)
    monkeypatch.setattr(sink_stac, "_table_columns", lambda c, v: [])
    # Both read GCS for a real object; unpatched they retry against a bucket this test has no
    # business touching, which is minutes of backoff, not a failure.
    monkeypatch.setattr(sink_stac.stac, "manual_override", lambda iid: {})
    monkeypatch.setattr(sink_stac.stac, "prior_property", lambda cp, iid, prop: None)
    monkeypatch.setattr(sink_stac.stac, "prior_file_fields", lambda cp, iid: {})
    monkeypatch.setattr(sink_stac.gcs, "exists", lambda p: False)
    monkeypatch.setattr(sink_stac.stac, "build_item",
                        lambda **k: captured.update(k) or {"assets": k["assets"]})
    monkeypatch.setattr(sink_stac.stac, "attach_renders", lambda i: None)
    monkeypatch.setattr(sink_stac.stac, "attach_classification", lambda i: None)
    monkeypatch.setattr(sink_stac.stac, "write_item", lambda i: "stac/path.json")

    sink_stac.write(Topic(schema="hazards", layer="hazards_qfaults_current"), None, "v")

    svc = [lk for lk in captured["extra_links"] if lk["rel"] == "service"]
    assert len(svc) == 1
    assert svc[0]["href"] == f"{sink_stac.config.PGF_BASE_URL}/collections/hazards_qfaults"


def test_sink_stac_wires_related(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(sink_stac, "_bbox", lambda c, v: [0, 1, 2, 3])
    monkeypatch.setattr(sink_stac, "_row_count", lambda c, v: 5)
    monkeypatch.setattr(sink_stac, "_table_columns", lambda c, v: [{"name": "uwi", "type": "string"}])
    # Keep the write path off GCS — unpatched, these read real objects and burn minutes
    # in retry backoff before falling back to their defaults.
    monkeypatch.setattr(sink_stac.stac, "manual_override", lambda iid: {})
    monkeypatch.setattr(sink_stac.stac, "prior_property", lambda cp, iid, prop: None)
    monkeypatch.setattr(sink_stac.stac, "prior_file_fields", lambda cp, iid: {})
    monkeypatch.setattr(sink_stac.gcs, "exists", lambda p: False)
    monkeypatch.setattr(sink_stac.stac, "build_item",
                        lambda **k: captured.update(k) or {"assets": k["assets"]})
    monkeypatch.setattr(sink_stac.stac, "attach_renders", lambda i: None)
    monkeypatch.setattr(sink_stac.stac, "attach_classification", lambda i: None)
    monkeypatch.setattr(sink_stac.stac, "write_item", lambda i: "stac/path.json")

    related_info = {
        "assets": {"boxes": {"href": "h", "type": "application/vnd.apache.parquet",
                             "roles": ["data", "related"], "title": "UCRC core boxes",
                             "table:columns": [{"name": "uwi"}]}},
        "links": [{"rel": "related", "href": "u", "type": "application/geo+json", "title": "quads"}],
        "foreign_keys": [{"fields": ["quad"],
                          "reference": {"resource": "mapping_quads_24k",
                                        "href": "https://cdn.example/quads.parquet",
                                        "fields": ["quad_id"]}}],
    }
    sink_stac.write(Topic(schema="energy_mineral", layer="enmin_ucrc_wells_current"),
                    None, "v", related=related_info)

    assets = captured["assets"]
    assert {"data", "pmtiles", "boxes"} <= set(assets)
    assert "ducklake" not in assets  # public catalog: DuckLake locator withheld (unreadable to public)
    assert assets["data"]["ugs:foreign_keys"] == related_info["foreign_keys"]  # FK on the data asset
    assert "related" in assets["boxes"]["roles"]
    # related link appended to the item's links; Table ext declared (boxes has table:columns).
    assert any(lk.get("rel") == "related" for lk in captured["extra_links"])
    assert sink_stac.stac.TABLE_EXT in captured["stac_extensions"]


def test_sink_stac_ducklake_review_only(monkeypatch):
    """The DuckLake locator is stamped only for the review catalog, never the public one."""
    captured: dict = {}
    monkeypatch.setattr(sink_stac, "_bbox", lambda c, v: [0, 1, 2, 3])
    monkeypatch.setattr(sink_stac, "_row_count", lambda c, v: 5)
    monkeypatch.setattr(sink_stac, "_table_columns", lambda c, v: [{"name": "uwi", "type": "string"}])
    # Keep the write path off GCS — unpatched, these read real objects and burn minutes
    # in retry backoff before falling back to their defaults.
    monkeypatch.setattr(sink_stac.stac, "manual_override", lambda iid: {})
    monkeypatch.setattr(sink_stac.stac, "prior_property", lambda cp, iid, prop: None)
    monkeypatch.setattr(sink_stac.stac, "prior_file_fields", lambda cp, iid: {})
    monkeypatch.setattr(sink_stac.gcs, "exists", lambda p: False)
    monkeypatch.setattr(sink_stac.stac, "build_item",
                        lambda **k: captured.update(k) or {"assets": k["assets"]})
    monkeypatch.setattr(sink_stac.stac, "attach_renders", lambda i: None)
    monkeypatch.setattr(sink_stac.stac, "attach_classification", lambda i: None)
    monkeypatch.setattr(sink_stac.stac, "write_item", lambda i: "stac/path.json")
    monkeypatch.setattr(sink_stac.config, "IS_REVIEW_CATALOG", True)

    sink_stac.write(Topic(schema="energy_mineral", layer="enmin_ucrc_wells_current"), None, "v")

    dl = captured["assets"]["ducklake"]
    assert dl["type"] == "application/x-ducklake-table"
    assert dl["href"].startswith("gs://") and dl["href"].endswith("energy_mineral/enmin_ucrc_wells")
