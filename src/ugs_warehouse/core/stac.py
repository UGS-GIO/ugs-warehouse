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

from . import config, gcs, iso, styles

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
               proj_epsg: int | None = None,
               collection_path: str | None = None) -> dict:
    """A STAC Item placed in the collection-nested layout.

    Lives at `{collection_path}/{id}/{id}.json` (default `collection_path` = `collection`,
    the flat one-level layout; pubs pass a nested `ugs-publications/<SERIES>` path). The
    root link climbs out to `{STAC_PREFIX}/catalog.json` — its depth follows the path.
    `extra_links` (web-map-links, via, cite-as, …) are appended; `proj_epsg` adds the
    projection extension + `proj:epsg` (the data's native CRS).
    """
    depth = (collection_path or collection).count("/") + 1  # collection dirs above the item dir
    root_rel = "../" * (depth + 1) + "catalog.json"          # + the item's own {id}/ dir
    links = [
        {"rel": "root", "href": root_rel, "type": "application/json"},
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
    # Private: the GCS layout path (may differ from the collection id for nested series).
    # Popped before serialization by write_item — never part of the published item.
    item["_collection_path"] = collection_path or collection
    return item


def _layout_path(item: dict) -> str:
    return item.get("_collection_path") or item["collection"]


def item_object_path(collection_path: str, item_id: str) -> str:
    """GCS path for an item. `collection_path` is the collection's layout path — a single
    segment for flat collections, or `ugs-publications/<SERIES>` for nested series."""
    return f"{config.STAC_PREFIX}/{collection_path}/{item_id}/{item_id}.json"


def attach_iso(item: dict) -> str:
    """Write an ISO 19139 sidecar next to the item + add a `metadata` asset (mutates item).

    For gov clearinghouses (data.gov / state portals) that harvest ISO, not STAC. Call
    before `write_item` so the written item references the sidecar; generated from the item
    as-is so the metadata asset is not yet present (no self-reference).
    """
    path = f"{config.STAC_PREFIX}/{_layout_path(item)}/{item['id']}/{item['id']}.iso.xml"
    gcs.put_bytes(iso.stac_to_iso19139(item).encode(), path,
                  content_type="application/xml", cache_control=gcs.CACHE_MUTABLE)
    item.setdefault("assets", {})["metadata"] = {
        "href": config.public_url(path), "type": "application/xml",
        "roles": ["metadata"], "title": "ISO 19139 metadata",
    }
    return path


def attach_renders(item: dict) -> None:
    """Attach the render extension + `renders` block (+ a vector `style` asset) by looking the
    item id up in the ugs-styles manifest. No-op when nothing matches (mutates item in place).

    Call before `write_item`. Best-effort: styling never blocks an ingest (see `core.styles`).
    """
    renders, style_asset = styles.renders_for(
        item["id"], set((item.get("assets") or {}).keys()))
    if not renders:
        return
    item.setdefault("properties", {})["renders"] = renders
    exts = item.setdefault("stac_extensions", [])
    if styles.RENDER_EXT not in exts:
        exts.append(styles.RENDER_EXT)
    if style_asset:
        item.setdefault("assets", {}).setdefault("style", style_asset)


def write_item(item: dict) -> str:
    """Serialize + upload an item. Mutable (overwritten per ingest) -> no-cache."""
    path = item_object_path(_layout_path(item), item["id"])
    item = {k: v for k, v in item.items() if k != "_collection_path"}  # drop private key
    gcs.put_bytes(json.dumps(item, indent=2).encode(), path,
                  content_type="application/geo+json", cache_control=gcs.CACHE_MUTABLE)
    return path


# ---------------------------------------------------------------- catalog refresh

def _group_items(paths: list[str]) -> dict[str, list[str]]:
    """{collection_path: [item_id, ...]} from object paths under STAC_PREFIX.

    Item layout is `<collection_path>/<id>/<id>.json`, where `collection_path` is one
    segment for flat collections (`ugs-serving-topics`) or two for nested series
    (`ugs-publications/<SERIES>`). catalog.json / collection.json / items.json are skipped.
    """
    out: dict[str, list[str]] = {}
    for path in paths:
        if not path.endswith(".json"):
            continue
        rel = path[len(config.STAC_PREFIX):].lstrip("/")
        parts = rel.split("/")
        if len(parts) < 3 or parts[-1] != f"{parts[-2]}.json":
            continue  # not an <collection_path>/<id>/<id>.json item
        out.setdefault("/".join(parts[:-2]), []).append(parts[-2])
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


def _collection_doc(collection: str, path: str, item_ids: list[str],
                    extent: dict | None = None, *, title: str | None = None,
                    service: bool | None = None) -> dict:
    """A collection.json at `{path}/collection.json`. `collection` is its STAC id (a series
    code like `DS` when nested, else the path). Root/parent links climb out per path depth;
    the OGC API Features link is added only for flat collections (serving topics — nested
    pub series aren't in featureserv) unless `service` is set explicitly."""
    depth = path.count("/") + 1
    if service is None:
        service = depth == 1
    doc = {
        "type": "Collection",
        "stac_version": STAC_VERSION,
        "id": collection,
        "title": title or prettify(collection),
        "description": f"UGS warehouse — {title or collection}.",
        "license": "proprietary",
        "extent": extent or {"spatial": {"bbox": [UTAH_BBOX]},
                             "temporal": {"interval": [[None, None]]}},
        "summaries": {"ugs:item_count": len(item_ids)},
        "links": [
            {"rel": "root", "href": "../" * depth + "catalog.json", "type": "application/json"},
            {"rel": "parent", "href": "../catalog.json", "type": "application/json"},
            {"rel": "self", "href": "./collection.json", "type": "application/json"},
            # Compact items index — one fetch for the whole list (viewers read this instead
            # of N item.json fetches; the per-item docs stay the source of truth for detail).
            {"rel": "items", "href": "./items.json", "type": "application/json", "title": "Items index"},
            *([{"rel": "service", "href": f"{PGF_BASE_URL}/collections/{collection}", "type": "application/json", "title": "OGC API Features endpoint"}] if service else []),
            *[{"rel": "item", "href": f"./{i}/{i}.json", "type": "application/geo+json"}
              for i in sorted(item_ids)],
        ],
    }
    return doc


def _child_link(href: str, title: str | None, count: int | None) -> dict:
    """A `rel=child` link carrying title + item count so a viewer can render the next level
    (catalog → children, with counts) from a single fetch. `ugs:item_count` is a non-standard
    hint; standard clients ignore it."""
    link = {"rel": "child", "href": href, "type": "application/json"}
    if title:
        link["title"] = title
    if count is not None:
        link["ugs:item_count"] = count
    return link


def _subcatalog_doc(catalog_id: str, children: list[dict], *, title: str | None = None,
                    description: str | None = None) -> dict:
    """A nesting Catalog (e.g. `ugs-publications`) whose children are per-series collections,
    each at `./<series>/collection.json`. `children` = [{id, title, count}, …]."""
    total = sum(c.get("count") or 0 for c in children)
    return {
        "type": "Catalog",
        "stac_version": STAC_VERSION,
        "id": catalog_id,
        "title": title or prettify(catalog_id),
        "description": description or f"UGS warehouse — {catalog_id}, by data series.",
        "summaries": {"ugs:item_count": total},
        "links": [
            {"rel": "root", "href": "../catalog.json", "type": "application/json"},
            {"rel": "parent", "href": "../catalog.json", "type": "application/json"},
            {"rel": "self", "href": "./catalog.json", "type": "application/json"},
            *[_child_link(f"./{c['id']}/collection.json", c.get("title"), c.get("count"))
              for c in sorted(children, key=lambda c: c["id"])],
        ],
    }


# Properties carried in the compact index — enough to render the list table + facets
# (title, date, series/type/topic/scale/author, keywords for search). The long
# `description`/citation is intentionally omitted; it loads with the full item on open.
_INDEX_PROP_KEYS = ("title", "datetime", "ugs:series_id", "ugs:series", "ugs:pub_type",
                    "ugs:topic", "ugs:scale", "ugs:author", "ugs:dbt_schema", "ugs:layer",
                    "ugs:row_count", "keywords")


def _index_entry(item: dict) -> dict:
    """A compact, list-renderable subset of a STAC item (mini-doc): id, bbox, a few
    properties, asset summaries, and any web-map links (pmtiles/cog) for map overlays."""
    props = item.get("properties") or {}
    entry: dict = {
        "id": item["id"],
        "bbox": item.get("bbox"),
        "properties": {k: props[k] for k in _INDEX_PROP_KEYS
                       if props.get(k) not in (None, "", [])},
    }
    assets = {
        k: {kk: a[kk] for kk in ("href", "type", "roles", "title") if a.get(kk) is not None}
        for k, a in (item.get("assets") or {}).items()
    }
    if assets:
        entry["assets"] = assets
    wlinks = [{kk: l[kk] for kk in ("rel", "href", "type", "pmtiles:layers") if l.get(kk) is not None}
              for l in (item.get("links") or []) if l.get("rel") in ("pmtiles", "cog")]
    if wlinks:
        entry["links"] = wlinks
    return entry


def _index_doc(collection: str, items: list[dict]) -> dict:
    entries = [_index_entry(it) for it in sorted(items, key=lambda it: it.get("id", ""))]
    return {"type": "ugs-items-index", "collection": collection,
            "count": len(entries), "items": entries}


def _root_doc(children: list[dict]) -> dict:
    """Root catalog. `children` = [{href, title, count}, …] — each top-level child is a flat
    collection's `collection.json` or a nesting sub-catalog's `catalog.json`."""
    return {
        "type": "Catalog",
        "stac_version": STAC_VERSION,
        "id": config.CATALOG_ID,
        "description": "UGS warehouse — cloud-native serving catalog across all producers "
                       "(vector serving topics, publications/COGs).",
        "links": [
            {"rel": "root", "href": "./catalog.json", "type": "application/json"},
            {"rel": "self", "href": "./catalog.json", "type": "application/json"},
            *[_child_link(c["href"], c.get("title"), c.get("count"))
              for c in sorted(children, key=lambda c: c["href"])],
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

    # 1. Write each leaf collection.json + items.json (flat or nested). collection id = the
    #    path's last segment (a series code when nested); title from the items' pub type.
    #    Record per-path {id, title, count} so the hierarchy links can carry counts.
    leaf: dict[str, dict] = {}
    for path, item_ids in groups.items():
        items = []
        for iid in sorted(item_ids):
            try:
                items.append(json.loads(gcs.get_bytes(item_object_path(path, iid)).decode()))
            except Exception:  # noqa: BLE001 — a missing/corrupt item shouldn't sink the refresh
                pass
        nested = "/" in path
        cid = path.split("/")[-1]
        title = next((it.get("properties", {}).get("ugs:pub_type") for it in items
                      if it.get("properties", {}).get("ugs:pub_type")), None) if nested else None
        _write_json(_collection_doc(cid, path, item_ids, _extent(items), title=title),
                    f"{config.STAC_PREFIX}/{path}/collection.json")
        _write_json(_index_doc(cid, items), f"{config.STAC_PREFIX}/{path}/items.json")
        leaf[path] = {"id": cid, "title": title or prettify(cid), "count": len(item_ids)}

    # 2. Build the hierarchy. A top-level segment with nested children (and no direct items)
    #    becomes a sub-catalog (e.g. ugs-publications → DS, OFR, … series collections);
    #    everything else is a flat collection directly under root. Child links carry counts.
    tops: dict[str, list[str]] = {}
    for path in groups:
        tops.setdefault(path.split("/")[0], []).append(path)
    root_children = []
    for top, paths in tops.items():
        if top not in groups:  # sub-catalog (nested, no direct items)
            kids = [leaf[p] for p in sorted(paths)]
            ptitle = prettify(top.replace("ugs-", ""))
            _write_json(_subcatalog_doc(top, kids, title=ptitle),
                        f"{config.STAC_PREFIX}/{top}/catalog.json")
            root_children.append({"href": f"./{top}/catalog.json", "title": ptitle,
                                  "count": sum(k["count"] for k in kids)})
        else:                  # flat collection
            root_children.append({"href": f"./{top}/collection.json",
                                  "title": leaf[top]["title"], "count": leaf[top]["count"]})
    _write_json(_root_doc(root_children), f"{config.STAC_PREFIX}/catalog.json")

    n = sum(len(v) for v in groups.values())
    print(f"[catalog] {config.public_url(config.STAC_PREFIX + '/catalog.json')} "
          f"({len(groups)} collections, {n} items)")
