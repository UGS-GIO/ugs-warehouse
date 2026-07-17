"""Review-catalog API — pure logic: the allow-list gate, href→object-path resolution, catalog crawl,
and asset-path extraction. Signing (obstore→IAM signBlob) and real bucket reads run on the deploy."""
import json

import pytest
from fastapi import HTTPException

from ugs_warehouse import review_catalog as rc
from ugs_warehouse.core import config

BASE = config.PUBLIC_BASE_URL.rstrip("/")
PREFIX = config.STAC_PREFIX


class _Req:
    def __init__(self, email):
        self.headers = {"x-goog-authenticated-user-email": f"accounts.google.com:{email}"}


def test_domain_gate_allows_domain_and_subdomains_only():
    assert rc._domain_ok("utah.gov")
    assert rc._domain_ok("dnr.utah.gov")       # subdomain
    assert rc._domain_ok("geology.utah.gov")
    assert not rc._domain_ok("notutah.gov")     # dot-boundary: NOT a subdomain
    assert not rc._domain_ok("gmail.com")


def test_require_reviewer_gates_by_domain():
    assert rc._require_reviewer(_Req("alice@utah.gov")) == "alice@utah.gov"
    with pytest.raises(HTTPException) as e:
        rc._require_reviewer(_Req("z@gmail.com"))
    assert e.value.status_code == 403


def test_object_path_absolute_relative_and_external():
    parent = f"{PREFIX}/hazards/collection.json"
    # absolute under our base → object path
    assert rc._object_path(f"{BASE}/review/pmtiles/x.pmtiles", parent) == "review/pmtiles/x.pmtiles"
    # relative → resolved against the parent's directory
    assert rc._object_path("./items.json", parent) == f"{PREFIX}/hazards/items.json"
    assert rc._object_path("../catalog.json", parent) == f"{PREFIX}/catalog.json"
    # some other host (CDN styles) → not ours to sign
    assert rc._object_path("https://cdn.example.org/styles/x.json", parent) is None


def test_asset_paths_only_surfaces_known_private_keys():
    item = {"assets": {
        "pmtiles": {"href": f"{BASE}/review/pmtiles/x.pmtiles"},
        "geoparquet": {"href": f"{BASE}/review/geoparquet/x.parquet"},
        "style": {"href": "https://cdn.example.org/styles/x.json"},  # CDN → excluded
        "bogus": {"href": f"{BASE}/review/other/x.bin"},                          # unknown key → excluded
    }}
    paths = rc._asset_paths(item, f"{PREFIX}/hazards/x/x.json")
    assert set(paths) == {"pmtiles", "geoparquet"}
    assert paths["pmtiles"] == "review/pmtiles/x.pmtiles"


def test_collect_items_uses_items_index_and_dedupes(monkeypatch):
    fs = {
        f"{PREFIX}/catalog.json": {"type": "Catalog", "id": "root",
            "links": [{"rel": "child", "href": "./hazards/collection.json"}]},
        f"{PREFIX}/hazards/collection.json": {"type": "Collection", "id": "hazards",
            "links": [{"rel": "item", "href": "./qfaults/qfaults.json"}]},  # should be skipped in favor of items.json
        f"{PREFIX}/hazards/items.json": {"type": "FeatureCollection", "items": [
            {"type": "Feature", "id": "hazards_qfaults", "collection": "hazards",
             "properties": {"title": "Quaternary Faults", "ugs:primary_key": "pk"}}]},
    }

    class _Get:
        def __init__(self, b): self._b = b
        def bytes(self): return self._b

    def fake_get(store, path):
        if path in fs:
            return _Get(json.dumps(fs[path]).encode())
        raise FileNotFoundError(path)

    monkeypatch.setattr(rc.obs, "get", fake_get)
    items = rc._collect_items()
    ids = [it["id"] for it, _ in items]
    assert ids == ["hazards_qfaults"]  # from items.json, and the rel=item link did NOT double-add
