#!/usr/bin/env python3
"""Build a LOCAL collections-layout STAC catalog under viewer/public/stac/.

The deployed ingest isn't reachable locally, so we reshape what's already published:
  - published vector items (warehouse/stac/ugs-serving-topics/*) -> ugs-serving-topics/<mart schema>
  - prod publication items (warehouse/stac/ugs-publications/*) -> ugs-publications

Items are rewritten into the nested layout the viewer expects
(<collection path>/<id>/<id>.json) and the collection.json / catalog.json docs are
generated with the same pure builders the real refresh uses. Serving topics split one level
by `ugs:dbt_schema`, matching the published catalog.
"""
from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.parse
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
PUBS = "ugs-publications"         # nesting catalog; children are publication series
PUBS_PER_SERIES = 40              # local dev only needs enough pubs to render, not all ~7k
OTHER_GROUP = "other"             # items the sandbox published without a ugs:dbt_schema
PROD = "https://maps-assets.geology.utah.gov/warehouse/stac"
OUT = Path(__file__).resolve().parents[1] / "public" / "stac"

STD_RELS = {"root", "parent", "collection", "self"}


def fetch(url: str) -> dict:
    # Published pub ids contain spaces ("GEOLOGIC MAP OF UTAH"), so hrefs must be percent-encoded
    # before they reach http.client — it rejects control/space characters in the request path.
    with urllib.request.urlopen(urllib.parse.quote(url, safe=":/?#[]@!$&'()*+,;=%"), timeout=30) as r:
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


def harvest_topics(base: str, catalog_id: str) -> dict[str, list[dict]]:
    """Published vector items → {`<catalog>/<schema>`: [item docs]}, the per-schema split.

    Reads the flat serving-topics collection (what's published today) and regroups it by
    `ugs:dbt_schema`, so local dev sees the nested layout before a reingest exists. Falls back to
    a bare catalog.json for the older flat-root layout.

    An item with no `ugs:dbt_schema` goes to an `other` collection instead of being dropped, and
    the count is printed — an unclassified layer should be visible, not silently missing.
    """
    try:
        cat, item_base = fetch(f"{base}/{catalog_id}/collection.json"), f"{base}/{catalog_id}"
    except urllib.error.HTTPError:
        cat, item_base = fetch(f"{base}/catalog.json"), base
    groups: dict[str, list[dict]] = {}
    for href in item_hrefs(cat, item_base):
        item = fetch(href)
        schema = ((item.get("properties") or {}).get("ugs:dbt_schema") or "").strip() or OTHER_GROUP
        path = f"{catalog_id}/{schema}"
        write_item(path, item)
        groups.setdefault(path, []).append(item)
        print(f"  + {path}/{item['id']}")
    if unclassified := groups.get(f"{catalog_id}/{OTHER_GROUP}"):
        print(f"  ! {len(unclassified)} item(s) with no ugs:dbt_schema -> {OTHER_GROUP}")
    return groups


def child_hrefs(doc: dict, base: str) -> list[str]:
    return [urllib.request.urljoin(base + "/", link["href"])
            for link in doc.get("links", []) if link["rel"] == "child"]


def harvest_collection(base: str, collection: str, limit: int | None = None) -> dict[str, list[dict]]:
    """Published pubs → {collection path: [item docs]}.

    Prod nests publications per series (`ugs-publications/<SERIES>`), so read the sub-catalog and
    walk its child collections; a flat `collection.json` still works for an un-nested catalog.
    `limit` caps items per series — the full set is thousands of fetches, and a local dev catalog
    only needs enough to render.
    """
    try:
        docs = [(collection, fetch(f"{base}/{collection}/collection.json"))]
    except urllib.error.HTTPError:
        cat = fetch(f"{base}/{collection}/catalog.json")
        docs = [(f"{collection}/{href.rstrip('/').split('/')[-2]}", fetch(href))
                for href in child_hrefs(cat, f"{base}/{collection}")]

    groups: dict[str, list[dict]] = {}
    for path, doc in docs:
        for href in item_hrefs(doc, f"{base}/{path}")[:limit]:
            item = fetch(href)
            write_item(path, item)
            groups.setdefault(path, []).append(item)
            print(f"  + {path}/{item['id']}")
    return groups


_INDEX_PROP_KEYS = ("title", "datetime", "ugs:series_id", "ugs:series", "ugs:pub_type",
                    "ugs:topic", "ugs:scale", "ugs:author", "ugs:county", "ugs:dbt_schema",
                    "ugs:layer", "ugs:row_count", "ugs:volume", "keywords")


def _index_entry(item: dict, *, rollup: bool = False) -> dict:
    """Mirror of core/stac's `_index_entry`: id, bbox, a property allowlist, asset SUMMARIES,
    a `rel:self` to the full item, and web-map links. Keep the two in step — a local catalog
    that carries more than production is a bug the laptop can't reproduce."""
    props = item.get("properties") or {}
    entry: dict = {
        "id": item["id"],
        "bbox": item.get("bbox"),
        "properties": {k: props[k] for k in _INDEX_PROP_KEYS
                       if props.get(k) not in (None, "", [])},
    }
    if props.get("ugs:renders"):
        entry["properties"]["ugs:renders"] = props["ugs:renders"]
    assets = {
        k: {kk: a[kk] for kk in ("href", "type", "roles", "title") if a.get(kk) is not None}
        for k, a in (item.get("assets") or {}).items()
    }
    if assets:
        entry["assets"] = assets
    sub = f"{item['collection']}/" if rollup and item.get("collection") else ""
    entry["links"] = [
        {"rel": "self", "href": f"./{sub}{item['id']}/{item['id']}.json",
         "type": "application/geo+json"},
        *[{kk: lnk[kk] for kk in ("rel", "href", "type", "pmtiles:layers") if lnk.get(kk) is not None}
          for lnk in (item.get("links") or []) if lnk.get("rel") in ("pmtiles", "cog")],
    ]
    return entry


def _index_doc(collection: str, items: list[dict], *, rollup: bool = False) -> dict:
    """The compact items index the viewer reads (cover strips, item lists, search, map overlays)
    without N item fetches. Entries are trimmed exactly as `refresh_catalog` trims them — carrying
    items whole here would let a consumer read metadata locally that production strips."""
    return {"type": "ugs-items-index", "collection": collection, "count": len(items),
            "items": [_index_entry(it, rollup=rollup)
                      for it in sorted(items, key=lambda it: it.get("id", ""))]}


def write_indexes(groups: dict[str, list[dict]]) -> None:
    """Per-collection items.json, plus the serving-topics rollup spanning every schema — the same
    two documents `refresh_catalog()` publishes. Without them the viewer falls back to per-item
    fetches, which feed the item list but NOT the cover strips (so thumbnails vanish)."""
    for path, items in groups.items():
        (OUT / path / "items.json").write_text(
            json.dumps(_index_doc(path.split("/")[-1], items), indent=2))
    rolled = [it for path, items in groups.items() if path.startswith(f"{TOPICS}/") for it in items]
    if rolled:
        (OUT / TOPICS / "items.json").write_text(
            json.dumps(_index_doc(TOPICS, rolled, rollup=True), indent=2))


def read_written_items() -> dict[str, list[dict]]:
    """{collection path: [item docs]} from what's already under OUT — lets `--indexes-only`
    rebuild the index documents without re-fetching the whole catalog."""
    groups: dict[str, list[dict]] = {}
    for item_path in OUT.rglob("*/*.json"):
        if item_path.stem != item_path.parent.name:  # not an <id>/<id>.json item doc
            continue
        path = item_path.parent.parent.relative_to(OUT).as_posix()
        groups.setdefault(path, []).append(json.loads(item_path.read_text()))
    return groups


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the local dev STAC catalog")
    ap.add_argument("--indexes-only", action="store_true",
                    help="rebuild items.json from the already-downloaded items (no fetching)")
    args = ap.parse_args()

    if args.indexes_only:
        groups = read_written_items()
        write_indexes(groups)
        print(f"[local] indexes rebuilt for {len(groups)} collection(s)")
        return

    print(f"vector -> {TOPICS}/<schema>")
    topic_groups = harvest_topics(PROD, TOPICS)
    print(f"pubs -> {PUBS}/<series>")
    pub_groups = harvest_collection(PROD, PUBS, limit=PUBS_PER_SERIES)
    item_groups: dict[str, list[dict]] = {**topic_groups, **pub_groups}
    groups = {path: [it["id"] for it in items] for path, items in item_groups.items()}

    for path, ids in groups.items():
        (OUT / path / "collection.json").write_text(
            json.dumps(_collection_doc(path, ids), indent=2))
    write_indexes(item_groups)
    # Both producers nest one level: a sub-catalog over their per-group collections.
    for parent, nested in ((TOPICS, topic_groups), (PUBS, pub_groups)):
        kids = [p.split("/")[-1] for p in nested if "/" in p]
        if kids:
            (OUT / parent / "catalog.json").write_text(
                json.dumps(_subcatalog_doc(parent, kids), indent=2))

    children = [(p, "catalog.json") for p in (TOPICS, PUBS) if (OUT / p / "catalog.json").exists()]
    children += [(p, "collection.json") for p in groups if "/" not in p]
    (OUT / "catalog.json").write_text(json.dumps(_root_doc(children), indent=2))

    n = sum(len(v) for v in groups.values())
    print(f"[local] {OUT}/catalog.json ({len(groups)} collections, {n} items)")


if __name__ == "__main__":
    main()
