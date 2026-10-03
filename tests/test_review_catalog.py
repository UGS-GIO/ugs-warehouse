"""Review-catalog API — pure logic: href→object-path resolution, catalog crawl, and asset-path
extraction. Reads are open to any authenticated user (no allow-list); signing (obstore→IAM signBlob)
and real bucket reads run on the deploy."""
import json

from ugs_warehouse import review_catalog as rc
from ugs_warehouse.core import config

BASE = config.PUBLIC_BASE_URL.rstrip("/")
PREFIX = config.STAC_PREFIX


def test_object_path_absolute_relative_and_external():
    parent = f"{PREFIX}/hazards/collection.json"
    # absolute under our base → object path
    assert rc._object_path(f"{BASE}/review/pmtiles/x.pmtiles", parent) == "review/pmtiles/x.pmtiles"
    # relative → resolved against the parent's directory
    assert rc._object_path("./items.json", parent) == f"{PREFIX}/hazards/items.json"
    assert rc._object_path("../catalog.json", parent) == f"{PREFIX}/catalog.json"
    # some other host (CDN styles) → not ours to sign
    assert rc._object_path("https://cdn.example.org/styles/x.json", parent) is None


def test_asset_paths_signs_every_on_bucket_asset_excludes_cdn():
    item = {"assets": {
        "pmtiles": {"href": f"{BASE}/review/pmtiles/x.pmtiles"},
        "geoparquet": {"href": f"{BASE}/review/geoparquet/x.parquet"},
        "ucrc_boxes": {"href": f"{BASE}/review/geoparquet/x_boxes.parquet"},  # related table → also signed
        "style": {"href": "https://cdn.example.org/styles/x.json"},          # CDN → left alone
    }}
    paths = rc._asset_paths(item, f"{PREFIX}/hazards/x/x.json")
    assert set(paths) == {"pmtiles", "geoparquet", "ucrc_boxes"}  # every on-bucket asset
    assert "style" not in paths                                   # off-base CDN href untouched
    assert paths["pmtiles"] == "review/pmtiles/x.pmtiles"


def test_sign_item_assets_rewrites_only_private_hrefs_and_preserves_item():
    item = {"type": "Feature", "id": "x", "properties": {"ugs:primary_key": "pk", "ugs:renders": {"a": {}}},
            "assets": {
                "pmtiles": {"href": f"{BASE}/review/pmtiles/x.pmtiles", "type": "application/vnd.pmtiles"},
                "style": {"href": "https://cdn.example.org/styles/x.json"}}}
    signed = {"review/pmtiles/x.pmtiles": "https://storage.googleapis.com/b/review/pmtiles/x.pmtiles?sig=1"}
    out = rc._sign_item_assets(item, f"{PREFIX}/hazards/x/x.json", signed)
    assert out["assets"]["pmtiles"]["href"] == signed["review/pmtiles/x.pmtiles"]
    assert out["assets"]["pmtiles"]["type"] == "application/vnd.pmtiles"     # other asset fields kept
    assert out["assets"]["style"]["href"] == "https://cdn.example.org/styles/x.json"  # CDN untouched
    assert out["properties"]["ugs:renders"] == {"a": {}}                     # full item preserved
    assert item["assets"]["pmtiles"]["href"].startswith(BASE)               # original not mutated


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

    # Patch the seam _read_json actually uses now — gcs.get_bytes (which handles the gzipped indexes
    # via its google-cloud fallback + gunzip). Patching raw obs.get would exercise the wrong path and
    # pass even if the reroute regressed; this fails if _read_json goes back to raw obstore. (#341)
    def fake_get_bytes(path):
        if path in fs:
            return json.dumps(fs[path]).encode()
        raise FileNotFoundError(path)

    monkeypatch.setattr(rc.gcs, "get_bytes", fake_get_bytes)
    items = rc._collect_items()
    ids = [it["id"] for it, _ in items]
    assert ids == ["hazards_qfaults"]  # from items.json, and the rel=item link did NOT double-add
