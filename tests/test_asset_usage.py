"""Per-asset usage metadata (ALL-5870 / #280).

A consumer or agent must be able to tell, from one item, which asset/endpoint is for display
vs query vs download. The machine purpose already rides the standard STAC `roles` (visual =
display, data = download, style); what was missing is a human `description` on each serving
asset, a `title` on the pmtiles / OGC-Features links (STAC links have no `roles`), and an
AGENTS.md that names the live OGC API Features query endpoint instead of denying it exists.

Roles are deliberately left unchanged here — adding display/download role strings would only
duplicate visual/data and diverge from STAC + Portolan.
"""
from __future__ import annotations

from ugs_warehouse.core import catalog_docs, config, stac
from ugs_warehouse.vector import sink_stac
from ugs_warehouse.vector import sink_stac as vec_sink
from ugs_warehouse.vector.topics import Topic


def _capture(monkeypatch, *, review: bool = False, thumb: bool = False) -> dict:
    """Run vec_sink.write with every DB/GCS touch stubbed, capturing the build_item kwargs."""
    captured: dict = {}
    monkeypatch.setattr(sink_stac, "_bbox", lambda c, v: [0, 1, 2, 3])
    monkeypatch.setattr(sink_stac, "_row_count", lambda c, v: 5)
    monkeypatch.setattr(sink_stac, "_table_columns", lambda c, v: [])
    monkeypatch.setattr(sink_stac.stac, "manual_override", lambda iid: {})
    monkeypatch.setattr(sink_stac.stac, "prior_property", lambda cp, iid, prop: None)
    monkeypatch.setattr(sink_stac.stac, "prior_file_fields", lambda cp, iid: {})
    monkeypatch.setattr(sink_stac.gcs, "exists", lambda p: thumb)
    monkeypatch.setattr(sink_stac.stac, "build_item",
                        lambda **k: captured.update(k) or {"assets": k["assets"]})
    monkeypatch.setattr(sink_stac.stac, "attach_renders", lambda i: None)
    monkeypatch.setattr(sink_stac.stac, "attach_classification", lambda i: None)
    monkeypatch.setattr(sink_stac.stac, "write_item", lambda i: "stac/path.json")
    if review:
        monkeypatch.setattr(sink_stac.config, "IS_REVIEW_CATALOG", True)
    sink_stac.write(Topic(schema="hazards", layer="hazards_qfaults_current"), None, "v")
    return captured


# ---------------------------------------------------------------- item assets

def test_data_and_pmtiles_assets_name_their_purpose(monkeypatch):
    """The download (GeoParquet) vs display (PMTiles) distinction, right on the asset."""
    assets = _capture(monkeypatch)["assets"]
    assert "download" in assets["data"]["description"].lower()
    assert "display" in assets["pmtiles"]["description"].lower()
    # Roles unchanged: the machine purpose still rides the standard STAC roles, nothing added.
    assert assets["data"]["roles"] == ["data"]
    assert assets["pmtiles"]["roles"] == ["visual"]


def test_thumbnail_asset_carries_a_description(monkeypatch):
    assets = _capture(monkeypatch, thumb=True)["assets"]
    assert "preview" in assets["thumbnail"]["description"].lower()


def test_ducklake_asset_carries_a_description_in_review(monkeypatch):
    assets = _capture(monkeypatch, review=True)["assets"]
    assert "analysis" in assets["ducklake"]["description"].lower()


# ---------------------------------------------------------------- item links

def test_pmtiles_link_names_display(monkeypatch):
    link = stac.pmtiles_link("http://x/a.pmtiles", ["lyr"])
    assert "display" in link["title"].lower()


def test_ogc_features_link_names_the_query_purpose(monkeypatch):
    links = _capture(monkeypatch)["extra_links"]
    svc = next(lk for lk in links if lk["rel"] == "service")
    assert "quer" in svc["title"].lower()   # query / queries / queryable


# ---------------------------------------------------------------- attached assets (core/stac)

def test_style_asset_gets_a_description(monkeypatch):
    style_asset = {"href": "https://x/s.json", "type": "application/json",
                   "roles": ["style"], "title": "Style"}
    monkeypatch.setattr(stac.styles, "renders_for",
                        lambda iid, keys: ({"default": {"style_url": "u"}}, dict(style_asset)))
    item = {"id": "x", "properties": {}, "assets": {}}
    stac.attach_renders(item)
    assert item["assets"]["style"]["description"]
    assert item["assets"]["style"]["roles"] == ["style"]   # role unchanged


# ---------------------------------------------------------------- AGENTS.md query endpoint

def test_agents_md_names_the_ogc_features_query_endpoint_when_served():
    md = catalog_docs.agents(title="Hazards", kind="collection",
                             path="ugs-serving-topics/hazards", children=3, items=[],
                             service=True).lower()
    assert "ogc api features" in md
    assert "quer" in md


def test_agents_md_does_not_advertise_a_query_endpoint_for_static_only_nodes():
    md = catalog_docs.agents(title="Data Series", kind="collection",
                             path="ugs-publications/DS", children=2, items=[],
                             service=False).lower()
    assert "ogc api features" not in md


def test_agents_md_for_a_catalog_node_points_at_query_services_without_denying_them():
    """A catalog node (root / sub-catalog) must not tell an agent 'no query endpoint' when served
    collections live underneath it — the #280 failure one level up, at the natural entry point."""
    md = catalog_docs.agents(title="UGS warehouse", kind="catalog", path="", children=3,
                             items=[], service=False).lower()
    assert "no query endpoint" not in md
    assert "ogc api features" in md   # points the agent at where the query services live


def _store(monkeypatch) -> dict:
    store: dict[str, bytes] = {}
    monkeypatch.setattr(stac.gcs, "put_bytes", lambda b, p, **k: store.__setitem__(p, b))
    monkeypatch.setattr(stac.gcs, "get_bytes", lambda p: store[p])
    monkeypatch.setattr(stac.gcs, "list_paths", lambda pre: [k for k in store if k.startswith(pre)])
    monkeypatch.setattr(stac.gcs, "exists", lambda p: p in store)
    monkeypatch.setattr(stac.config, "EXTERNAL_CATALOGS", [])
    monkeypatch.setattr(stac, "attach_renders", lambda item: None)
    monkeypatch.setattr(stac.styles, "warm", lambda: None)
    return store


def test_refresh_wires_the_query_endpoint_note_to_served_collections_only(monkeypatch):
    """End-to-end: a serving-topic collection's AGENTS.md names OGC API Features; a pubs one
    (static-only, no featureserv) must not — the `service` flag has to reach agents() correctly."""
    store = _store(monkeypatch)
    stac.write_item(stac.build_item(
        item_id="hazards_qfaults", collection="hazards",
        collection_path=vec_sink.collection_path("hazards"),
        geometry=stac.bbox_polygon([-114, 37, -109, 42]), bbox=[-114, 37, -109, 42],
        datetime_iso="2026-01-01T00:00:00Z", properties={"title": "Quaternary Faults"},
        assets={"data": {"href": "https://x/q.parquet", "type": config.PARQUET_MIME,
                         "roles": ["data"]}}))
    stac.write_item(stac.build_item(
        item_id="DS-8", collection="DS", collection_path="ugs-publications/DS",
        geometry=stac.bbox_polygon([0, 1, 2, 3]), bbox=[0, 1, 2, 3],
        datetime_iso="2026-01-01T00:00:00Z", properties={"title": "A publication"},
        assets={"data": {"href": "https://x/a.parquet", "type": config.PARQUET_MIME,
                         "roles": ["data"]}}))
    stac.refresh_catalog()
    p = config.STAC_PREFIX
    serving = store[f"{p}/ugs-serving-topics/hazards/AGENTS.md"].decode().lower()
    pubs = store[f"{p}/ugs-publications/DS/AGENTS.md"].decode().lower()
    assert "ogc api features" in serving
    assert "ogc api features" not in pubs
