"""A regression fence: build a catalog with the real builders, then validate it with rashid.

This checks OUR CODE, not the published bucket. The builders write a small catalog into a temp
directory through the same `core.stac` path an ingest uses, and rashid reads it back.

`GUARDED` names the rules we have actually fixed. Anything outside that set is reported but not
enforced, because the remaining failures are open decisions rather than regressions, such as the
collection-level thumbnails and single-file collections that depend on the layout question (#257). Widen
`GUARDED` as those land; never widen it to something we have not fixed, or the fence stops meaning
anything.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ugs_warehouse.core import config, stac
from ugs_warehouse.pubs import sink_stac as pubs_sink
from ugs_warehouse.vector import sink_stac as vec_sink

rashid = pytest.importorskip("rashid", reason="rashid is an optional dev dependency")

# Rules this repo has fixed and must not break again. Each maps to work already merged.
GUARDED = {
    "PTL-GEN-000",  # a readable root catalog.json
    "PTL-CNF-001",  # every catalog and collection declares the Portolan profile
    "PTL-SCH-001",  # and validates against it
    "PTL-STR-001",  # valid STAC 1.1.0 — the keyword list and the empty-string properties (#246)
    "PTL-TTL-001",  # every catalog and collection has a title and description (#216)
    "PTL-TTL-003",  # every child and item link carries a title (#216)
    "PTL-LNK-001",  # root and parent links
    "PTL-LNK-002",  # a child or item link for everything a node contains
    "PTL-LNK-006",  # every structural link resolves to the right object (#249, and the prune)
    "PTL-BBX-001",  # finite WGS84 bounding boxes
    "PTL-TMP-002",  # RFC 3339 datetimes, start before end
    "PTL-AST-001",  # every asset has a media type and a role
    "PTL-AST-002",  # absolute asset hrefs use https (#249)
    "PTL-AST-006",  # a publisher's plain TIFF is a `source` asset, exempt from the COG requirement
    "PTL-FIL-001",  # README.md + AGENTS.md beside every catalog and collection
    "PTL-FIL-002",  # AGENTS.md linked rel:"agents"
    "PTL-FIL-003",  # README.md linked rel:"describedby"
    "PTL-FIL-004",  # the README is non-empty and carries a title heading
    "PTL-LIC-001",  # an SPDX license on every collection
    "PTL-LIC-003",  # never the deprecated "proprietary"
    "PTL-PRV-001",  # at least one producer
    "PTL-PRV-002",  # exactly one host, listed last
}


def _catalog_on_disk(monkeypatch, tmp_path: Path) -> Path:
    """Write a representative catalog through the real builders and return its root."""
    store: dict[str, bytes] = {}
    monkeypatch.setattr(stac.gcs, "put_bytes", lambda b, p, **k: store.__setitem__(p, b))
    monkeypatch.setattr(stac.gcs, "get_bytes", lambda p: store[p])
    monkeypatch.setattr(stac.gcs, "list_paths", lambda pre: [k for k in store if k.startswith(pre)])
    monkeypatch.setattr(stac.gcs, "exists", lambda p: p in store)
    monkeypatch.setattr(stac.config, "EXTERNAL_CATALOGS", [])  # federated roots are not ours to validate
    monkeypatch.setattr(stac, "attach_iso", lambda item: "")   # the sidecar is XML, not a STAC object
    monkeypatch.setattr(stac, "attach_renders", lambda item: None)
    monkeypatch.setattr(stac.styles, "warm", lambda: None)

    # A vector topic: GeoParquet + PMTiles + the file fields the writes report (#245).
    topic_assets = {
        "data": {"href": config.public_url(config.archive_path("hazards_qfaults")),
                 "type": config.PARQUET_MIME, "roles": ["data"], "title": "GeoParquet archive",
                 "file:size": 4096, "file:checksum": "1220" + "ab" * 32},
        "pmtiles": {"href": config.public_url("warehouse/pmtiles/hazards_qfaults/hazards_qfaults.pmtiles"),
                    "type": config.PMTILES_MIME, "roles": ["visual"], "title": "PMTiles vector tiles"},
    }
    stac.write_item(stac.build_item(
        item_id="hazards_qfaults", collection="hazards",
        collection_path=vec_sink.collection_path("hazards"),
        geometry=stac.bbox_polygon([-114.0, 37.0, -109.0, 42.0]), bbox=[-114.0, 37.0, -109.0, 42.0],
        datetime_iso="2026-01-01T00:00:00Z",
        properties={"title": "Quaternary Faults", "ugs:dbt_schema": "hazards"},
        assets=topic_assets, proj_epsg=4326,
        extra_links=[stac.pmtiles_link(topic_assets["pmtiles"]["href"], ["hazards_qfaults"])]))

    # A publication, through the pubs builder so its properties and links are the real ones.
    stac.write_item(pubs_sink.build_item(
        {"series_id": "OFR-593", "series": "OFR", "pub_year": "2011", "pub_publisher": "UGS",
         "pub_name": "Interim geologic map of the Rush Valley quadrangle",
         "full_citation": "Clark, D.L., 2011, Interim geologic map of the Rush Valley quadrangle.",
         "keywords": "Geology--Utah--Tooele County--Maps", "pub_url": "ofr/ofr-593.pdf"},
        [], override={}))

    # A publication whose source gives nothing beyond a name — the shape that used to publish
    # empty strings and a null-ish datetime.
    stac.write_item(pubs_sink.build_item(
        {"series_id": "MD-50", "series": "MD", "pub_year": "1954", "pub_publisher": "",
         "pub_name": "Mining district file 50"}, [], override={}))

    # A mining district file whose only file is the publisher's plain TIFF scan.
    stac.write_item(pubs_sink.build_item(
        {"series_id": "MD-1002", "series": "MD", "pub_year": "1956", "pub_publisher": "",
         "pub_name": "Geophysical Sonic Log", "pub_url": "uranium_data/MD01002.tif"}, [], override={}))

    # An edition pair: a current map and its deprecated predecessor, both with a COG (exercises the
    # version/deprecated properties, predecessor/successor/latest links, and the currency-gated
    # related link — ALL-5954).
    _current_href = config.public_url(stac.item_object_path(
        pubs_sink.collection_group({"series_id": "M-296DM"}) + "/M", "M-296DM"))
    stac.write_item(pubs_sink.build_item(
        {"series_id": "M-296DM", "series": "M", "pub_year": "2022", "pub_publisher": "UGS",
         "pub_name": "Geologic map of the Park City East quadrangle", "pub_scale": "1:24,000"},
        [], has_cog=True,
        edition={"version": "2022", "deprecated": False, "predecessor_href": None,
                 "successor_href": None, "latest_href": None}, override={}))
    stac.write_item(pubs_sink.build_item(
        {"series_id": "GQ-852", "series": "GQ", "pub_year": "1971", "pub_publisher": "USGS",
         "pub_name": "Geologic map of the Park City East quadrangle", "pub_scale": "1:24,000"},
        [], has_cog=True,
        edition={"version": "1971", "deprecated": True, "predecessor_href": None,
                 "successor_href": _current_href, "latest_href": _current_href}, override={}))

    stac.refresh_catalog()

    root = tmp_path / "catalog"
    prefix = config.STAC_PREFIX + "/"
    for path, body in store.items():
        if not path.startswith(prefix):
            continue
        out = root / path[len(prefix):]
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(body)
    return root


def test_the_builders_produce_a_catalog_that_passes_the_rules_we_have_fixed(monkeypatch, tmp_path):
    root = _catalog_on_disk(monkeypatch, tmp_path)

    report = rashid.validate(root, data=False, schema=True)
    broken = [f for f in report.findings if f.rule_id in GUARDED]

    assert not broken, "\n".join(
        f"{f.rule_id} {f.severity.value if hasattr(f.severity, 'value') else f.severity} "
        f"{f.path}: {f.message}" for f in broken)


def test_the_fence_names_rules_rashid_still_has(monkeypatch, tmp_path):
    """A rule renamed or dropped upstream would silently stop being enforced."""
    known = set(rashid.CHECKS)
    assert GUARDED <= known, f"no longer in rashid: {sorted(GUARDED - known)}"


def test_the_catalog_the_fence_builds_is_the_shape_we_publish(monkeypatch, tmp_path):
    """Guards the fixture itself: a fence around an empty or flat catalog proves nothing."""
    root = _catalog_on_disk(monkeypatch, tmp_path)

    assert (root / "catalog.json").exists()
    # Nested catalogs above leaf collections, which is the layout both producers write.
    assert (root / "ugs-serving-topics" / "hazards" / "collection.json").exists()
    assert (root / "ugs-publications" / "OFR" / "OFR-593" / "OFR-593.json").exists()
    items = json.loads((root / "ugs-serving-topics" / "hazards" / "collection.json").read_text())
    assert [lk for lk in items["links"] if lk["rel"] == "item"]


def _bad_catalog(tmp_path: Path) -> Path:
    """A catalog carrying faults this repo has already fixed once."""
    root = tmp_path / "bad"
    (root / "COLL" / "X").mkdir(parents=True)
    (root / "catalog.json").write_text(json.dumps({
        "type": "Catalog", "stac_version": "1.1.0", "id": "r", "title": "R", "description": "d",
        "links": [{"rel": "root", "href": "./catalog.json", "type": "application/json"},
                  # no title on the child link (#216)
                  {"rel": "child", "href": "./COLL/collection.json", "type": "application/json"}]}))
    (root / "COLL" / "collection.json").write_text(json.dumps({
        "type": "Collection", "stac_version": "1.1.0", "id": "COLL", "title": "C", "description": "d",
        "license": "CC-BY-4.0",   # no providers at all
        "extent": {"spatial": {"bbox": [[-114, 37, -109, 42]]},
                   "temporal": {"interval": [[None, None]]}},
        "links": [{"rel": "root", "href": "../catalog.json", "type": "application/json"},
                  {"rel": "parent", "href": "../catalog.json", "type": "application/json"},
                  {"rel": "item", "href": "./X/X.json", "type": "application/geo+json"}]}))
    (root / "COLL" / "X" / "X.json").write_text(json.dumps({
        "type": "Feature", "stac_version": "1.1.0", "id": "X", "collection": "COLL",
        "geometry": None,
        # the two values that produced 12,791 structural errors (#246)
        "properties": {"datetime": "2026-01-01T00:00:00Z", "description": "", "keywords": ""},
        "assets": {},
        "links": [{"rel": "root", "href": "../../catalog.json", "type": "application/json"},
                  {"rel": "parent", "href": "../collection.json", "type": "application/json"},
                  {"rel": "collection", "href": "../collection.json", "type": "application/json"}]}))
    return root


def test_the_fence_fails_on_the_faults_it_exists_for(tmp_path):
    """A fence that cannot fail proves nothing. These are real regressions this repo has shipped:
    an empty description and a string `keywords` (#246), a child link with no title (#216), and a
    collection with no providers."""
    report = rashid.validate(_bad_catalog(tmp_path), data=False)
    caught = {f.rule_id for f in report.findings} & GUARDED

    assert {"PTL-STR-001", "PTL-TTL-003", "PTL-PRV-001"} <= caught
