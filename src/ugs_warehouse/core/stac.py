"""Shared STAC — item builder, collections hierarchy, derive-from-truth catalog refresh.

Both producers emit items through `build_item` + `write_item`, into ONE catalog laid out
with collections:

    {STAC_PREFIX}/catalog.json                     root  -> child collections
    {STAC_PREFIX}/{collection}/collection.json      coll  -> items
    {STAC_PREFIX}/{collection}/{id}/{id}.json        item

`refresh_catalog()` lists GCS and rebuilds the root + every collection.json from what's
actually there (idempotent, concurrency-tolerant) — no manual regen, no shared-file races.
"""
from __future__ import annotations

import json
import re

from . import config, gcs, iso

PGF_BASE_URL = config.PGF_BASE_URL

STAC_VERSION = "1.0.0"
# web-map-links: lets STAC Browser v4+ render the layer (not just the footprint).
WEB_MAP_LINKS_EXT = "https://stac-extensions.github.io/web-map-links/v1.3.0/schema.json"
# projection: declares the data's native CRS (proj:epsg).
PROJ_EXT = "https://stac-extensions.github.io/projection/v1.1.0/schema.json"


# ---------------------------------------------------------------- helpers

def bbox_polygon(bbox: list[float]) -> dict:
    """A GeoJSON Polygon ring from a [minx, miny, maxx, maxy] bbox."""
    minx, miny, maxx, maxy = bbox
    return {
        "type": "Polygon",
        "coordinates": [[[minx, miny], [maxx, miny], [maxx, maxy],
                         [minx, maxy], [minx, miny]]],
    }


def prettify(stem: str) -> str:
    """Fallback human title from a snake/kebab id — `geothermal_kgra` -> `Geothermal Kgra`.

    Deliberately dumb (no acronym map = no hidden registry); curated titles come from
    upstream (schema_registry display_name / pub_name) and are passed in explicitly.
    """
    return re.sub(r"[_\-]+", " ", stem).strip().title()


def pmtiles_link(href: str, layers: list[str] | None = None) -> dict:
    """A web-map-links `pmtiles` link so STAC Browser draws the vector layer."""
    link = {"rel": "pmtiles", "href": href, "type": "application/vnd.pmtiles"}
    if layers:
        link["pmtiles:layers"] = layers
    return link


def cog_link(href: str) -> dict:
    """A web-map-links `cog` link so STAC Browser draws the raster layer."""
    return {"rel": "cog", "href": href, "type": "image/tiff; application=geotiff; profile=cloud-optimized"}


# ---------------------------------------------------------------- items

def build_item(*, item_id: str, collection: str, geometry: dict | None,
               bbox: list[float] | None, datetime_iso: str | None,
               properties: dict, assets: dict,
               extra_links: list[dict] | None = None,
               stac_extensions: list[str] | None = None,
               proj_epsg: int | None = None) -> dict:
    """A STAC Item placed in the collection-nested layout.

    Adds the standard root/parent/self links relative to `{collection}/{id}/{id}.json`;
    `extra_links` (web-map-links, via, cite-as, …) are appended. `proj_epsg` adds the
    projection extension + `proj:epsg` (the data's native CRS).
    """
    links = [
        {"rel": "root", "href": "../../catalog.json", "type": "application/json"},
        {"rel": "parent", "href": "../collection.json", "type": "application/json"},
        {"rel": "collection", "href": "../collection.json", "type": "application/json"},
        {"rel": "self", "href": f"./{item_id}.json", "type": "application/geo+json"},
        *(extra_links or []),
    ]
    props = {"datetime": datetime_iso, **properties}
    exts = list(stac_extensions or [])
    if proj_epsg is not None:
        props["proj:epsg"] = proj_epsg
        if PROJ_EXT not in exts:
            exts.append(PROJ_EXT)
    item = {
        "type": "Feature",
        "stac_version": STAC_VERSION,
        "id": item_id,
        "collection": collection,
        "geometry": geometry,
        "bbox": bbox,
        "properties": props,
        "assets": assets,
        "links": links,
    }
    if exts:
        item["stac_extensions"] = exts
    return item


def item_object_path(collection: str, item_id: str) -> str:
    return f"{config.STAC_PREFIX}/{collection}/{item_id}/{item_id}.json"


def attach_iso(item: dict) -> str:
    """Write an ISO 19139 sidecar next to the item + add a `metadata` asset (mutates item).

    For gov clearinghouses (data.gov / state portals) that harvest ISO, not STAC. Call
    before `write_item` so the written item references the sidecar; generated from the item
    as-is so the metadata asset is not yet present (no self-reference).
    """
    path = f"{config.STAC_PREFIX}/{item['collection']}/{item['id']}/{item['id']}.iso.xml"
    gcs.put_bytes(iso.stac_to_iso19139(item).encode(), path,
                  content_type="application/xml", cache_control=gcs.CACHE_MUTABLE)
    item.setdefault("assets", {})["metadata"] = {
        "href": config.public_url(path), "type": "application/xml",
        "roles": ["metadata"], "title": "ISO 19139 metadata",
    }
    return path


def write_item(item: dict) -> str:
    """Serialize + upload an item. Mutable (overwritten per ingest) -> no-cache."""
    path = item_object_path(item["collection"], item["id"])
    gcs.put_bytes(json.dumps(item, indent=2).encode(), path,
                  content_type="application/geo+json", cache_control=gcs.CACHE_MUTABLE)
    return path


# ---------------------------------------------------------------- catalog refresh

def _group_items(paths: list[str]) -> dict[str, list[str]]:
    """{collection: [item_id, ...]} from object paths under STAC_PREFIX.

    Item layout is `<collection>/<id>/<id>.json`; root `catalog.json` and the
    per-collection `collection.json` are skipped.
    """
    out: dict[str, list[str]] = {}
    for path in paths:
        if not path.endswith(".json"):
            continue
        rel = path[len(config.STAC_PREFIX):].lstrip("/")
        parts = rel.split("/")
        if len(parts) != 3 or parts[2] != f"{parts[1]}.json":
            continue  # not an <collection>/<id>/<id>.json item
        out.setdefault(parts[0], []).append(parts[1])
    return out


UTAH_BBOX = [-114.1, 36.9, -108.9, 42.1]  # fallback when items carry no bbox


def _extent(items: list[dict]) -> dict:
    """Real spatial + temporal extent from the collection's items (union bbox, min/max
    datetime). Falls back to the Utah bbox if no item bboxes are present."""
    bxs = [it["bbox"] for it in items if it.get("bbox") and len(it["bbox"]) >= 4]
    dts = sorted(it["properties"]["datetime"] for it in items
                 if it.get("properties", {}).get("datetime"))
    bbox = ([min(b[0] for b in bxs), min(b[1] for b in bxs),
             max(b[2] for b in bxs), max(b[3] for b in bxs)] if bxs else UTAH_BBOX)
    interval = [[dts[0], dts[-1]]] if dts else [[None, None]]
    return {"spatial": {"bbox": [bbox]}, "temporal": {"interval": interval}}


def _collection_doc(collection: str, item_ids: list[str], extent: dict | None = None) -> dict:
    return {
        "type": "Collection",
        "stac_version": STAC_VERSION,
        "id": collection,
        "description": f"UGS warehouse — {collection}.",
        "license": "proprietary",
        "extent": extent or {"spatial": {"bbox": [UTAH_BBOX]},
                             "temporal": {"interval": [[None, None]]}},
        "links": [
            {"rel": "root", "href": "../catalog.json", "type": "application/json"},
            {"rel": "self", "href": "./collection.json", "type": "application/json"},
            {"rel": "service", "href": f"{PGF_BASE_URL}/collections/{collection}", "type": "application/json", "title": "OGC API Features endpoint"},
            *[{"rel": "item", "href": f"./{i}/{i}.json", "type": "application/geo+json"}
              for i in sorted(item_ids)],
        ],
    }


def _root_doc(collections: list[str]) -> dict:
    return {
        "type": "Catalog",
        "stac_version": STAC_VERSION,
        "id": config.CATALOG_ID,
        "description": "UGS warehouse — cloud-native serving catalog across all producers "
                       "(vector serving topics, publications/COGs).",
        "links": [
            {"rel": "root", "href": "./catalog.json", "type": "application/json"},
            {"rel": "self", "href": "./catalog.json", "type": "application/json"},
            *[{"rel": "child", "href": f"./{c}/collection.json", "type": "application/json"}
              for c in sorted(collections)],
        ],
    }


def _write_json(doc: dict, object_path: str) -> None:
    gcs.put_bytes(json.dumps(doc, indent=2).encode(), object_path,
                  content_type="application/json", cache_control=gcs.CACHE_MUTABLE)


def refresh_catalog() -> None:
    """Rebuild the root catalog + every collection.json by listing items in GCS.

    Derive-from-truth + idempotent: safe to run after each ingest or on demand. Under
    concurrent ingests the last writer wins (brief staleness, self-heals next run).
    """
    groups = _group_items(gcs.list_paths(config.STAC_PREFIX))
    for collection, item_ids in groups.items():
        # Read each item to derive the real spatial/temporal extent (bbox union, datetimes).
        items = []
        for iid in sorted(item_ids):
            try:
                items.append(json.loads(gcs.get_bytes(item_object_path(collection, iid)).decode()))
            except Exception:  # noqa: BLE001 — a missing/corrupt item shouldn't sink the refresh
                pass
        _write_json(_collection_doc(collection, item_ids, _extent(items)),
                    f"{config.STAC_PREFIX}/{collection}/collection.json")
    _write_json(_root_doc(list(groups)), f"{config.STAC_PREFIX}/catalog.json")
    n = sum(len(v) for v in groups.values())
    print(f"[catalog] {config.public_url(config.STAC_PREFIX + '/catalog.json')} "
          f"({len(groups)} collections, {n} items)")
