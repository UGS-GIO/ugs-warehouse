#!/usr/bin/env python3
"""Build a LOCAL collections-layout STAC catalog under viewer/public/stac/.

The deployed ingest isn't reachable locally, so we reshape what's already published:
  - 20 flat vector items  (warehouse-sandbox/stac/*.json)  -> ugs-serving-topics/<mart schema>
  - prod publication items (warehouse/stac/ugs-publications/*) -> ugs-publications

Items are rewritten into the nested layout the viewer expects
(<collection path>/<id>/<id>.json) and the collection.json / catalog.json docs are
generated with the same pure builders the real refresh uses. Serving topics split one level
by `ugs:dbt_schema`, matching the published catalog.
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


def _collection_doc(path: str, item_ids: list[str]) -> dict:
    """`path` is the layout path — one segment (flat) or two (`ugs-serving-topics/hazards`).
    Root/parent links climb out per depth; the OGC API Features link is flat-only, as in core."""
    depth = path.count("/") + 1
    collection = path.split("/")[-1]
    return {
        "type": "Collection",
        "stac_version": STAC_VERSION,
        "id": collection,
        "description": f"UGS warehouse — {collection}.",
        "license": "proprietary",
        "extent": {"spatial": {"bbox": [[-114.1, 36.9, -108.9, 42.1]]},
                   "temporal": {"interval": [[None, None]]}},
        "links": [
            {"rel": "root", "href": "../" * depth + "catalog.json", "type": "application/json"},
            {"rel": "parent", "href": "../catalog.json", "type": "application/json"},
            {"rel": "self", "href": "./collection.json", "type": "application/json"},
            *([{"rel": "service", "href": f"{PGF_BASE_URL}/collections/{collection}",
                "type": "application/json", "title": "OGC API Features endpoint"}] if depth == 1 else []),
            *[{"rel": "item", "href": f"./{i}/{i}.json", "type": "application/geo+json"}
              for i in sorted(item_ids)],
        ],
    }


def _subcatalog_doc(catalog_id: str, child_ids: list[str]) -> dict:
    """A nesting Catalog whose children are collections at `./<id>/collection.json`."""
    return {
        "type": "Catalog",
        "stac_version": STAC_VERSION,
        "id": catalog_id,
        "description": f"UGS warehouse — {catalog_id}.",
        "links": [
            {"rel": "root", "href": "../catalog.json", "type": "application/json"},
            {"rel": "parent", "href": "../catalog.json", "type": "application/json"},
            {"rel": "self", "href": "./catalog.json", "type": "application/json"},
            *[{"rel": "child", "href": f"./{c}/collection.json", "type": "application/json"}
              for c in sorted(child_ids)],
        ],
    }


def _root_doc(children: list[tuple[str, str]]) -> dict:
    """`children` = [(path segment, doc filename)] — a flat collection or a nesting sub-catalog."""
    return {
        "type": "Catalog",
        "stac_version": STAC_VERSION,
        "id": CATALOG_ID,
        "description": "UGS warehouse — cloud-native serving catalog across all producers "
                       "(vector serving topics, publications/COGs).",
        "links": [
            {"rel": "root", "href": "./catalog.json", "type": "application/json"},
            {"rel": "self", "href": "./catalog.json", "type": "application/json"},
            *[{"rel": "child", "href": f"./{seg}/{doc}", "type": "application/json"}
              for seg, doc in sorted(children)],
        ],
    }


TOPICS = "ugs-serving-topics"     # nesting catalog; children are mart schemas
OTHER_GROUP = "other"             # items the sandbox published without a ugs:dbt_schema
SANDBOX = "https://maps-assets.geology.utah.gov/warehouse-sandbox/stac"
PROD = "https://maps-assets.geology.utah.gov/warehouse/stac"
OUT = Path(__file__).resolve().parents[1] / "public" / "stac"

STD_RELS = {"root", "parent", "collection", "self"}


def fetch(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.load(r)


def nested_links(item_id: str, original: list[dict], depth: int = 1) -> list[dict]:
    """Standard nested links + preserved web-map / via / cite-as links. `depth` = collection
    path segments above the item's own folder (2 for `ugs-serving-topics/<schema>`)."""
    extra = [link for link in original if link.get("rel") not in STD_RELS]
    return [
        {"rel": "root", "href": "../" * (depth + 1) + "catalog.json", "type": "application/json"},
        {"rel": "parent", "href": "../collection.json", "type": "application/json"},
        {"rel": "collection", "href": "../collection.json", "type": "application/json"},
        {"rel": "self", "href": f"./{item_id}.json", "type": "application/geo+json"},
        *extra,
    ]


def write_item(coll_path: str, item: dict) -> None:
    iid = item["id"]
    item["collection"] = coll_path.split("/")[-1]
    item["links"] = nested_links(iid, item.get("links", []), coll_path.count("/") + 1)
    d = OUT.joinpath(*coll_path.split("/"), iid)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{iid}.json").write_text(json.dumps(item, indent=2))


def item_hrefs(catalog: dict, base: str) -> list[str]:
    return [urllib.request.urljoin(base + "/", link["href"])
            for link in catalog.get("links", []) if link["rel"] == "item"]


def harvest_topics(base: str, catalog_id: str) -> dict[str, list[str]]:
    """Sandbox's flat vector items → {`<catalog>/<schema>`: [item ids]}.

    An item with no `ugs:dbt_schema` (older sandbox writes predate the property) goes to an
    `other` collection instead of being dropped, and the count is printed — an unclassified
    layer should be visible, not silently missing from the local catalog."""
    cat = fetch(f"{base}/catalog.json")
    groups: dict[str, list[str]] = {}
    for href in item_hrefs(cat, base):
        item = fetch(href)
        schema = ((item.get("properties") or {}).get("ugs:dbt_schema") or "").strip() or OTHER_GROUP
        path = f"{catalog_id}/{schema}"
        write_item(path, item)
        groups.setdefault(path, []).append(item["id"])
        print(f"  + {path}/{item['id']}")
    if unclassified := groups.get(f"{catalog_id}/{OTHER_GROUP}"):
        print(f"  ! {len(unclassified)} item(s) with no ugs:dbt_schema -> {OTHER_GROUP}")
    return groups


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
    print(f"vector -> {TOPICS}/<schema>")
    topic_groups = harvest_topics(SANDBOX, TOPICS)
    print("pubs -> ugs-publications")
    groups: dict[str, list[str]] = {**topic_groups,
                                    "ugs-publications": harvest_collection(PROD, "ugs-publications")}

    for path, ids in groups.items():
        (OUT / path / "collection.json").write_text(
            json.dumps(_collection_doc(path, ids), indent=2))
    # Serving topics nest one level: a sub-catalog over the per-schema collections.
    (OUT / TOPICS / "catalog.json").write_text(json.dumps(
        _subcatalog_doc(TOPICS, [p.split("/")[-1] for p in topic_groups]), indent=2))

    children = [(TOPICS, "catalog.json")]
    children += [(p, "collection.json") for p in groups if "/" not in p]
    (OUT / "catalog.json").write_text(json.dumps(_root_doc(children), indent=2))

    n = sum(len(v) for v in groups.values())
    print(f"[local] {OUT}/catalog.json ({len(groups)} collections, {n} items)")


if __name__ == "__main__":
    main()
