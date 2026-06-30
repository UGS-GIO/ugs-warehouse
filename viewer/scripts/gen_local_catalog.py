#!/usr/bin/env python3
"""Build a LOCAL collections-layout STAC catalog under viewer/public/stac/.

The deployed ingest isn't reachable locally, so we reshape what's already published:
  - 20 flat vector items  (warehouse-sandbox/stac/*.json)  -> ugs-serving-topics
  - prod publication items (warehouse/stac/ugs-publications/*) -> ugs-publications

Items are rewritten into the nested layout the viewer expects
(<collection>/<id>/<id>.json) and the collection.json / catalog.json docs are
generated with the same pure builders the real refresh uses.
"""
from __future__ import annotations

import json
import urllib.request
from pathlib import Path

# Inlined copies of core/stac's pure doc builders. We don't import core/stac
# directly because it pulls in core/gcs -> obstore, which isn't in the local env.
STAC_VERSION = "1.0.0"
CATALOG_ID = "ugs-warehouse"
PGF_BASE_URL = "https://api.geology.utah.gov"


def _collection_doc(collection: str, item_ids: list[str]) -> dict:
    return {
        "type": "Collection",
        "stac_version": STAC_VERSION,
        "id": collection,
        "description": f"UGS warehouse — {collection}.",
        "license": "proprietary",
        "extent": {"spatial": {"bbox": [[-114.1, 36.9, -108.9, 42.1]]},
                   "temporal": {"interval": [[None, None]]}},
        "links": [
            {"rel": "root", "href": "../catalog.json", "type": "application/json"},
            {"rel": "self", "href": "./collection.json", "type": "application/json"},
            {"rel": "service", "href": f"{PGF_BASE_URL}/collections/{collection}",
             "type": "application/json", "title": "OGC API Features endpoint"},
            *[{"rel": "item", "href": f"./{i}/{i}.json", "type": "application/geo+json"}
              for i in sorted(item_ids)],
        ],
    }


def _root_doc(collections: list[str]) -> dict:
    return {
        "type": "Catalog",
        "stac_version": STAC_VERSION,
        "id": CATALOG_ID,
        "description": "UGS warehouse — cloud-native serving catalog across all producers "
                       "(vector serving topics, publications/COGs).",
        "links": [
            {"rel": "root", "href": "./catalog.json", "type": "application/json"},
            {"rel": "self", "href": "./catalog.json", "type": "application/json"},
            *[{"rel": "child", "href": f"./{c}/collection.json", "type": "application/json"}
              for c in sorted(collections)],
        ],
    }


SANDBOX = "https://maps-assets.geology.utah.gov/warehouse-sandbox/stac"
PROD = "https://maps-assets.geology.utah.gov/warehouse/stac"
OUT = Path(__file__).resolve().parents[1] / "public" / "stac"

STD_RELS = {"root", "parent", "collection", "self"}


def fetch(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.load(r)


def nested_links(item_id: str, original: list[dict]) -> list[dict]:
    """Standard nested links + preserved web-map / via / cite-as links."""
    extra = [link for link in original if link.get("rel") not in STD_RELS]
    return [
        {"rel": "root", "href": "../../catalog.json", "type": "application/json"},
        {"rel": "parent", "href": "../collection.json", "type": "application/json"},
        {"rel": "collection", "href": "../collection.json", "type": "application/json"},
        {"rel": "self", "href": f"./{item_id}.json", "type": "application/geo+json"},
        *extra,
    ]


def write_item(collection: str, item: dict) -> None:
    iid = item["id"]
    item["collection"] = collection
    item["links"] = nested_links(iid, item.get("links", []))
    d = OUT / collection / iid
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{iid}.json").write_text(json.dumps(item, indent=2))


def item_hrefs(catalog: dict, base: str) -> list[str]:
    return [urllib.request.urljoin(base + "/", link["href"])
            for link in catalog.get("links", []) if link["rel"] == "item"]


def harvest_flat(base: str, collection: str) -> list[str]:
    cat = fetch(f"{base}/catalog.json")
    ids = []
    for href in item_hrefs(cat, base):
        item = fetch(href)
        write_item(collection, item)
        ids.append(item["id"])
        print(f"  + {collection}/{item['id']}")
    return ids


def harvest_collection(base: str, collection: str) -> list[str]:
    coll = fetch(f"{base}/{collection}/collection.json")
    ids = []
    for href in item_hrefs(coll, f"{base}/{collection}"):
        item = fetch(href)
        write_item(collection, item)
        ids.append(item["id"])
        print(f"  + {collection}/{item['id']}")
    return ids


def main() -> None:
    groups: dict[str, list[str]] = {}
    print("vector -> ugs-serving-topics")
    groups["ugs-serving-topics"] = harvest_flat(SANDBOX, "ugs-serving-topics")
    print("pubs -> ugs-publications")
    groups["ugs-publications"] = harvest_collection(PROD, "ugs-publications")

    for collection, ids in groups.items():
        (OUT / collection / "collection.json").write_text(
            json.dumps(_collection_doc(collection, ids), indent=2))
    (OUT / "catalog.json").write_text(
        json.dumps(_root_doc(list(groups)), indent=2))

    n = sum(len(v) for v in groups.values())
    print(f"[local] {OUT}/catalog.json ({len(groups)} collections, {n} items)")


if __name__ == "__main__":
    main()
