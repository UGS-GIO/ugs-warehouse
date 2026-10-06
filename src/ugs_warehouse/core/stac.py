"""Shared STAC — item builder, collections hierarchy, derive-from-truth catalog refresh.

Both producers emit items through `build_item` + `write_item`, into ONE catalog laid out
with collections:

    {STAC_PREFIX}/catalog.json                     root  -> child collections
    {STAC_PREFIX}/{collection}/collection.json      coll  -> items
    {STAC_PREFIX}/{collection}/{id}/{id}.json        item

A collection may nest one level — a sub-catalog whose children are collections
(`ugs-publications/<SERIES>`, `ugs-serving-topics/<SCHEMA>`). Nesting sub-catalogs listed in
`ROLLUP_INDEX_CATALOGS` also publish an items.json spanning every child, so one-fetch consumers
keep a single URL for "all of X".

`refresh_catalog()` lists GCS and rebuilds the root + every collection.json from what's
actually there (idempotent, concurrency-tolerant) — no manual regen, no shared-file races.
"""
from __future__ import annotations

import json
import re
import sys

from . import catalog_docs, config, feature_service, gcs, item_mirror, styles
from .bbox import to_2d_bbox

PGF_BASE_URL = config.PGF_BASE_URL

# Vector topics nest under this catalog, one collection per mart schema (see vector.sink_stac).
SERVING_TOPICS_CATALOG = "ugs-serving-topics"
# Raster scenes nest under this catalog, one collection per layer (see raster.identity).
RASTER_CATALOG = "ugs-rasters"
# Root catalog blurb, shared by catalog.json and the README beside it.
ROOT_DESCRIPTION = ("UGS warehouse — cloud-native serving catalog across all producers "
                    "(vector serving topics, publications/COGs).")

# Link rel for our compact items.json index. Deliberately not "items" — see _collection_doc.
INDEX_REL = "ugs-items-index"

STAC_VERSION = "1.1.0"  # 1.1 promotes `bands` + data_type/nodata to common metadata (no raster ext)
# The Portolan profile every catalog and collection declares conformance to (rashid PTL-CNF-001).
PORTOLAN_SCHEMA = "https://schemas.portolan-sdi.org/portolan/v0.2.0/schema.json"
# web-map-links: lets STAC Browser v4+ render the layer (not just the footprint).
WEB_MAP_LINKS_EXT = "https://stac-extensions.github.io/web-map-links/v1.3.0/schema.json"
# projection: v2.0.0 → `proj:code` ("EPSG:xxxx"), replacing the deprecated `proj:epsg`.
PROJ_EXT = "https://stac-extensions.github.io/projection/v2.0.0/schema.json"
# table: standard column description for tabular assets (`table:columns`) — data asset + related tables.
TABLE_EXT = "https://stac-extensions.github.io/table/v1.2.0/schema.json"
# classification: machine-readable categories (value/name/color) for categorical layers.
CLASSIFICATION_EXT = "https://stac-extensions.github.io/classification/v2.0.0/schema.json"
# alternate-assets: a second location for the SAME bytes (`alternate`), used where we mirror a file
# and keep the publisher's own copy addressable alongside our CDN href.
ALTERNATE_ASSETS_EXT = "https://stac-extensions.github.io/alternate-assets/v1.2.0/schema.json"
# file: `file:size` + `file:checksum` on the assets we write — a consumer budgets the fetch and
# verifies what it got. Declared only when an asset actually carries one (see `file_fields`).
FILE_EXT = "https://stac-extensions.github.io/file/v2.1.0/schema.json"
# version: `version`/`deprecated` on an item — declared only when an item actually carries one
# of those properties (see `build_item`).
VERSION_EXT = "https://stac-extensions.github.io/version/v1.2.0/schema.json"

# Per-asset usage hints — the human half of "which asset is for what" (display / query / download).
# The machine half already rides the standard STAC `roles` (visual = display, data = download,
# style), so these are ONLY the `description` a person or agent reads to pick an endpoint without
# guessing (#280). One home for the wording so the two producers that stamp them can't drift; kept
# short (a label, not a how-to — the runnable how-to lives once in the collection AGENTS.md).
USAGE_DATA = "GeoParquet — full dataset; download, or read in place with DuckDB for analysis"
USAGE_PMTILES = "Vector tiles — web-map display"
USAGE_DUCKLAKE = "DuckLake table — versioned SQL analysis (review catalog only)"
USAGE_THUMBNAIL = "Styled preview image"
USAGE_STYLE = "MapLibre GL style — how to draw this layer"


def has_feature_service(collection_path: str) -> bool:
    """True when the collection at `collection_path` is served live by OGC API Features
    (featureserv/) — the flat collections and the serving-topic schemas. Single source for the
    collection's `rel:service` link (`_collection_doc`), the AGENTS.md query-endpoint note
    (`catalog_docs.agents`) and the service's layer list (`feature_service`), so they can't
    disagree."""
    return "/" not in collection_path or collection_path.startswith(f"{SERVING_TOPICS_CATALOG}/")


# ---------------------------------------------------------------- helpers

def bbox_polygon(bbox: list[float]) -> dict:
    """A GeoJSON Polygon ring from a 2D or 3D STAC bbox."""
    minx, miny, maxx, maxy = to_2d_bbox(bbox)
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
    """A web-map-links `pmtiles` link so STAC Browser draws the vector layer.

    Titled for its purpose: STAC links carry no `roles`, so the human `title` is where a consumer
    learns this endpoint is the fast display path — as opposed to the OGC API Features query link
    and the GeoParquet download asset (#280)."""
    link = {"rel": "pmtiles", "href": href, "type": "application/vnd.pmtiles",
            "title": USAGE_PMTILES}
    if layers:
        link["pmtiles:layers"] = layers
    return link


# ---------------------------------------------------------------- items

def file_fields(meta: gcs.FileMeta | None) -> dict:
    """`{file:size, file:checksum}` for an asset dict, from what the write reported.

    Omits the checksum when the writer couldn't compute one (a server-side copy). The STAC
    file extension makes both SHOULD, and an absent value beats a fabricated one.
    """
    if meta is None:
        return {}
    return {"file:size": meta.size, **({"file:checksum": meta.checksum} if meta.checksum else {})}


def object_path_of(href: str) -> str | None:
    """The bucket object path behind a CDN href, or None for an off-warehouse href."""
    base = config.PUBLIC_BASE_URL.rstrip("/") + "/"
    return href.removeprefix(base).split("?", 1)[0] if href.startswith(base) else None


def stamp_file_meta(item: dict, index: dict[str, gcs.FileMeta] | None = None) -> int:
    """Add `file:size`/`file:checksum` to assets in our bucket that lack them (mutates item).

    `index` is `{object_path: FileMeta}` from `gcs.list_file_meta`; when None, each missing asset is
    looked up on its own (a raster layer's directory can hold thousands of COGs). A checksum already on the asset wins; a size-only asset (a server-side
    copy) takes the stored checksum. Returns the number of assets stamped.
    """
    missing = {k: p for k, a in (item.get("assets") or {}).items()
               if "file:checksum" not in a and (p := object_path_of(a.get("href", "")))}
    if index is None:
        index = {p: m for p in missing.values() if (m := gcs.get_file_meta(p)) is not None}
    n = 0
    for key, path in missing.items():
        asset, fields = item["assets"][key], file_fields(index.get(path))
        if fields and ("file:checksum" in fields or "file:size" not in asset):
            asset.update(fields)
            n += 1
    if n and FILE_EXT not in (exts := item.setdefault("stac_extensions", [])):
        exts.append(FILE_EXT)
    return n


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
    projection extension + `proj:code` (the data's CRS).
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
    # Declared from what the assets carry, not by the caller: every producer stamps file fields
    # through `file_fields`, and an asset with none (an off-warehouse href) must not force the ext.
    if any("file:size" in a or "file:checksum" in a for a in assets.values()) and FILE_EXT not in exts:
        exts.append(FILE_EXT)
    # Declared from what the properties carry, same rule as FILE_EXT above: a caller sets
    # `version`/`deprecated` (pubs editions) and the extension follows, rather than every caller
    # having to remember to declare it itself.
    if ("version" in props or "deprecated" in props) and VERSION_EXT not in exts:
        exts.append(VERSION_EXT)
    if proj_epsg is not None:
        # projection ext v2.0.0: `proj:code` ("EPSG:4326") replaces the deprecated `proj:epsg`.
        props["proj:code"] = f"EPSG:{proj_epsg}"
        if PROJ_EXT not in exts:
            exts.append(PROJ_EXT)
    item = {
        "type": "Feature",
        "stac_version": STAC_VERSION,
        "id": item_id,
        "collection": collection,
        "geometry": geometry,
        # STAC: bbox is REQUIRED when geometry is non-null, and must be ABSENT (not null) when
        # geometry is null (aspatial pubs). A literal `bbox: null` fails item-spec validation.
        **({"bbox": bbox} if geometry is not None and bbox is not None else {}),
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


def override_object(item_id: str) -> str:
    """GCS path of an item's hand-authored metadata override (keyed by the exact STAC item id)."""
    return f"{config.OVERRIDES_PREFIX}/{item_id}.json"


def patch_item_properties(object_path: str, updates: dict) -> bool:
    """Merge `updates` into a published item's `properties` in place — an instant edit that shows
    immediately, without a full reingest (the durable source stays the override sidecar; a later
    reingest reapplies it). Best-effort: returns False if the item can't be read."""
    try:
        item = json.loads(gcs.get_bytes(object_path))
    except Exception:  # noqa: BLE001 — item not published yet / unreadable
        return False
    item.setdefault("properties", {}).update(updates)
    # Same cache policy as write_item so an edited item keeps a consistent header (not flipped to
    # no-cache until the next reingest). Operator sees the edit at once (bucket read); public via
    # the CDN within the short max-age.
    # NOT gzipped — see write_item: a gzipped item breaks every obstore get_bytes read. (#341)
    gcs.put_bytes(json.dumps(item, indent=2).encode(), object_path,
                  content_type="application/geo+json", cache_control=gcs.CACHE_CATALOG)
    return True


def manual_override(item_id: str) -> dict:
    """Hand-authored metadata overrides (e.g. {description, title, source:"manual"}) for an item, or
    {} if none. Ingest prefers these over source metadata — an operator sets a value the source can't
    give, and it wins + survives reingest. Read next to build_item, same as prior_property."""
    try:
        return json.loads(gcs.get_bytes(override_object(item_id))) or {}
    except Exception:  # noqa: BLE001 — no override / unreadable
        return {}


def prior_property(collection_path: str, item_id: str, prop: str):
    """Value of `prop` in the currently-published item.json, or None if the item doesn't exist yet or
    can't be read. Lets a reingest PRESERVE a field (e.g. description) when the incoming metadata omits
    it — last-non-empty wins, so a resubmit with no description keeps the old one instead of blanking it."""
    try:
        item = json.loads(gcs.get_bytes(item_object_path(collection_path, item_id)))
        return (item.get("properties") or {}).get(prop)
    except Exception:  # noqa: BLE001 — first ingest / missing / unreadable → no prior value
        return None


def prior_file_fields(collection_path: str, item_id: str) -> dict[str, dict]:
    """`{asset_key: {file:size, file:checksum}}` from the currently-published item, or `{}`.

    A `--skip-unchanged` run rewrites the item without running the data sinks, so it has no
    write to take the fields from. The artifacts are unchanged — that is what the run
    established — so the published values still describe them. Carrying them forward keeps an
    unchanged topic from losing the fields the last full ingest stamped.
    """
    try:
        item = json.loads(gcs.get_bytes(item_object_path(collection_path, item_id)))
    except Exception:  # noqa: BLE001 — first ingest / missing / unreadable → nothing to carry
        return {}
    out = {}
    for key, asset in (item.get("assets") or {}).items():
        fields = {k: asset[k] for k in ("file:size", "file:checksum") if k in asset}
        if fields:
            out[key] = fields
    return out


def attach_renders(item: dict) -> None:
    """Attach a `ugs:renders` block (+ a vector `style` asset) by looking the item id up in the
    ugs-styles manifest. No-op when nothing matches (mutates item in place).

    Call before `write_item`. Best-effort: styling never blocks an ingest (see `core.styles`).

    NOT the STAC render extension: that extension is raster/titiler-oriented, and its v2.0.0 schema
    *requires* a `rel:"render"` image link whenever web-map-links is also present — but we render
    vector layers client-side from a MapLibre GL `style_url`, so we have no render-image endpoint.
    `ugs:renders` is the UGS-prefixed equivalent (identical shape): spec-legal, no schema to fail.
    The standard `roles:["style"]` `style` asset is the interoperable pointer to the GL style.
    """
    renders, style_asset = styles.renders_for(
        item["id"], set((item.get("assets") or {}).keys()))
    if not renders:
        return
    item.setdefault("properties", {})["ugs:renders"] = renders
    if style_asset:
        # A usage `description` alongside the standard `style` role, so the item alone says the
        # style asset is the "how to draw it" endpoint (#280). Preserve one the styles module set;
        # don't mutate its dict (renders_for may hand back a shared/cached asset).
        item.setdefault("assets", {}).setdefault(
            "style", {**style_asset, "description": style_asset.get("description") or USAGE_STYLE})


def attach_classification(item: dict) -> None:
    """Attach `classification:classes` (categorical value/name/color) derived from the bound GL
    style's default vector render. No-op for unstyled / uniform / raster items (mutates in place).

    Call AFTER attach_renders (reads the `ugs:renders` block). Best-effort: styling never blocks ingest.
    """
    renders = (item.get("properties") or {}).get("ugs:renders") or {}
    render = renders.get("default") or next(iter(renders.values()), None)
    style_url = (render or {}).get("style_url")
    if not style_url:
        return
    classes = styles.classification_classes(style_url)
    if not classes:
        return
    item.setdefault("properties", {})["classification:classes"] = classes
    exts = item.setdefault("stac_extensions", [])
    if CLASSIFICATION_EXT not in exts:
        exts.append(CLASSIFICATION_EXT)


def write_item(item: dict) -> str:
    """Serialize + upload an item. Overwritten per ingest → short-lived edge cache + SWR."""
    path = item_object_path(_layout_path(item), item["id"])
    item = {k: v for k, v in item.items() if k != "_collection_path"}  # drop private key
    # Items are ~6 KB and are read back individually (refresh_catalog, prior_property, overrides).
    # NOT gzipped: GCS serves a Content-Encoding:gzip object with decompressive transcoding, which
    # strips Content-Length — the header obstore.get() requires — so a gzipped item silently fails
    # every read (dropped from items.json, blanked descriptions). Only the big rollup/catalog indexes
    # gzip (see _write_json), where the ~25:1 saving is worth the get_bytes fallback that reads them.
    gcs.put_bytes(json.dumps(item, indent=2).encode(), path,
                  content_type="application/geo+json", cache_control=gcs.CACHE_CATALOG)
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
    bxs = [to_2d_bbox(it["bbox"]) for it in items if it.get("bbox") and len(it["bbox"]) in (4, 6)]
    dts = sorted(it["properties"]["datetime"] for it in items
                 if it.get("properties", {}).get("datetime"))
    bbox = ([min(b[0] for b in bxs), min(b[1] for b in bxs),
             max(b[2] for b in bxs), max(b[3] for b in bxs)] if bxs else UTAH_BBOX)
    interval = [[dts[0], dts[-1]]] if dts else [[None, None]]
    return {"spatial": {"bbox": [bbox]}, "temporal": {"interval": interval}}


def _collection_doc(collection: str, path: str, item_ids: list[str],
                    extent: dict | None = None, *, title: str | None = None,
                    service: bool | None = None, mappable: int | None = None,
                    description: str | None = None,
                    item_titles: dict[str, str] | None = None,
                    assets: dict | None = None) -> dict:
    """A collection.json at `{path}/collection.json`. `collection` is its STAC id (a series
    code like `DS` when nested, else the path). Root/parent links climb out per path depth;
    the OGC API Features link is added only for flat collections (serving topics — nested
    pub series aren't in featureserv) unless `service` is set explicitly."""
    depth = path.count("/") + 1
    if service is None:
        # Flat collections, plus the nested serving-topic schemas — those are precisely the
        # collections whose items featureserv binds, and they'd otherwise be the only ones with
        # no pointer to the service that serves them.
        service = has_feature_service(path)
    doc = {
        "type": "Collection",
        "stac_version": STAC_VERSION,
        "stac_extensions": [PORTOLAN_SCHEMA],
        "id": collection,
        "title": title or prettify(collection),
        "description": description or f"UGS warehouse — {title or collection}.",
        # Utah state geo data is CC-BY-4.0 by UGRC policy (not "proprietary"). SPDX id + a
        # rel:license link below; providers names UGS as producer/licensor/host.
        "license": config.DATA_LICENSE,
        "providers": config.PROVIDERS,
        "extent": extent or {"spatial": {"bbox": [UTAH_BBOX]},
                             "temporal": {"interval": [[None, None]]}},
        # Counts are UGS-prefixed top-level extras, NOT `summaries` — STAC summaries values must be
        # arrays/ranges/JSON-Schema (they summarize item *property* ranges), so a scalar count there
        # fails strict validation. A prefixed top-level field is spec-legal (additionalProperties).
        "ugs:item_count": len(item_ids),
        **({"ugs:mappable_count": mappable} if mappable is not None else {}),
        # Collection-level assets. STAC allows them and Portolan requires some of them (a
        # thumbnail on a geospatial collection); a collection with nothing to carry omits the key
        # rather than publishing an empty object.
        **({"assets": assets} if assets else {}),
        "links": [
            {"rel": "root", "href": "../" * depth + "catalog.json", "type": "application/json"},
            {"rel": "parent", "href": "../catalog.json", "type": "application/json"},
            {"rel": "self", "href": "./collection.json", "type": "application/json"},
            # Compact items index — one fetch for the whole list (viewers read this instead
            # of N item.json fetches; the per-item docs stay the source of truth for detail).
            # NOT rel:"items": STAC API reserves that for an ItemCollection endpoint, and a
            # standard client (STAC Browser) that follows it here fails the whole collection
            # with "not a valid list of STAC Items". Those clients use the rel:"item" links below.
            {"rel": INDEX_REL, "href": "./items.json", "type": "application/json", "title": "Items index"},
            {"rel": "license", "href": config.LICENSE_URL, "type": "text/html", "title": config.DATA_LICENSE},
            # Portolan requires both files beside every node, linked from the JSON. refresh_catalog
            # writes them; a link without its file is a broken link, so the two move together.
            *catalog_docs.markdown_links(),
            # Service ROOT, not `/collections/{collection}`: featureserv names its collections after
            # STAC *item* ids (`hazards_qfaults`), so a per-collection path never existed on any
            # host. The queryable per-layer link lives on the item instead (vector.sink_stac).
            *([{"rel": "service", "href": f"{PGF_BASE_URL}/collections", "type": "application/json", "title": "OGC API Features service"}] if service else []),
            # Titled, so listing a collection needs no fetch per item; Portolan requires one.
            *[{"rel": "item", "href": f"./{i}/{i}.json", "type": "application/geo+json",
               "title": (item_titles or {}).get(i) or i}
              for i in sorted(item_ids)],
        ],
    }
    return doc


def _collection_assets(path: str, items: list[dict], mirror: object | None = None) -> dict:
    """Collection-level assets derived from the collection's own items.

    Portolan requires a thumbnail on a geospatial collection (PTL-VIZ-001), and a collection whose
    items are all one kind of thing can borrow an item's: the scenes of a raster dataset, or the
    publications of one series. Not a serving-topic collection: that is a dbt schema holding
    unrelated layers, so one layer's preview would misrepresent the other eleven. That is the grain
    question (#257), not something a thumbnail should paper over.

    The item is the most recent one that has a thumbnail, ties broken by id, so the preview tracks
    what was published last instead of whichever item happened to sort first.
    """
    mirror_asset = item_mirror.asset(path, mirror)
    top = path.split("/", 1)[0]
    if top != RASTER_CATALOG and top not in PUB_SERIES_CATALOGS:
        return mirror_asset
    with_thumbs = [it for it in items if (it.get("assets") or {}).get("thumbnail", {}).get("href")]
    if not with_thumbs:
        return mirror_asset
    newest = max(with_thumbs, key=lambda it: (it.get("properties", {}).get("datetime") or "", it["id"]))
    thumb = newest["assets"]["thumbnail"]
    title = newest.get("properties", {}).get("title") or prettify(newest["id"])
    return {"thumbnail": {"href": thumb["href"], "type": thumb.get("type", "image/png"),
                          "roles": ["thumbnail"], "title": f"Preview: {title}"},
            **mirror_asset}


def _is_mappable(item: dict) -> bool:
    """True if the item has something to draw on a map — a COG asset or a pmtiles/cog web-map link.
    Drives the per-collection `ugs:mappable_count` so a viewer can flag groups with no map data
    without fetching their items."""
    for a in (item.get("assets") or {}).values():
        t, h, roles = a.get("type") or "", a.get("href") or "", a.get("roles") or []
        if "cloud-optimized" in t or "cloud-optimized" in roles or h.endswith(".cog.tif"):
            return True
    return any(lnk.get("rel") in ("pmtiles", "cog") for lnk in (item.get("links") or []))


def _child_link(href: str, title: str | None, count: int | None, mappable: int | None = None) -> dict:
    """A `rel=child` link carrying title + item count (+ mappable count) so a viewer can render the
    next level (catalog → children, with counts) from a single fetch. The `ugs:` hints are
    non-standard; standard clients ignore them."""
    link = {"rel": "child", "href": href, "type": "application/json"}
    if title:
        link["title"] = title
    if count is not None:
        link["ugs:item_count"] = count
    if mappable is not None:
        link["ugs:mappable_count"] = mappable
    return link


def _subcatalog_doc(catalog_id: str, children: list[dict], *, title: str | None = None,
                    description: str | None = None, items_index: bool = False) -> dict:
    """A nesting Catalog (e.g. `ugs-publications`) whose children are per-series collections,
    each at `./<series>/collection.json`. `children` = [{id, title, count}, …].

    `items_index` adds a `rel:items` link to a rollup index spanning every child collection —
    see ROLLUP_INDEX_CATALOGS."""
    total = sum(c.get("count") or 0 for c in children)
    total_mappable = sum(c.get("mappable") or 0 for c in children)
    return {
        "type": "Catalog",
        "stac_version": STAC_VERSION,
        "stac_extensions": [PORTOLAN_SCHEMA],
        "id": catalog_id,
        "title": title or prettify(catalog_id),
        "description": description or f"UGS warehouse — {catalog_id}, by data series.",
        # Top-level prefixed extras (NOT `summaries`, which Catalogs don't even define) — see _collection_doc.
        "ugs:item_count": total,
        "ugs:mappable_count": total_mappable,
        "links": [
            {"rel": "root", "href": "../catalog.json", "type": "application/json"},
            {"rel": "parent", "href": "../catalog.json", "type": "application/json"},
            {"rel": "self", "href": "./catalog.json", "type": "application/json"},
            *catalog_docs.markdown_links(),
            *([{"rel": INDEX_REL, "href": "./items.json", "type": "application/json",
                "title": "Items index (all child collections)"}] if items_index else []),
            *[_child_link(f"./{c['id']}/collection.json", c.get("title"), c.get("count"), c.get("mappable"))
              for c in sorted(children, key=lambda c: c["id"])],
        ],
    }


# Properties carried in the compact index — enough to render the list table + facets
# (title, date, series/type/topic/scale/author, keywords for search). The long
# `description`/citation is intentionally omitted; it loads with the full item on open.
_INDEX_PROP_KEYS = ("title", "datetime", "ugs:series_id", "ugs:series", "ugs:pub_type",
                    "ugs:topic", "ugs:scale", "ugs:author", "ugs:county", "ugs:dbt_schema",
                    "ugs:layer", "ugs:row_count", "ugs:volume", "keywords")


def _index_entry(item: dict, *, rollup: bool = False, prefix: str | None = None) -> dict:
    """A compact, list-renderable subset of a STAC item (mini-doc): id, bbox, a few
    properties, asset summaries, a `rel:self` pointer to the full item, and any web-map
    links (pmtiles/cog) for map overlays.

    Assets are SUMMARIES — href/type/roles/title only. Anything a consumer needs beyond
    that (`ugs:foreign_keys`, `table:columns`, …) lives on the full item; follow `self`
    rather than widening this allowlist, which every list view pays for.
    """
    props = item.get("properties") or {}
    entry: dict = {
        "id": item["id"],
        "bbox": item.get("bbox"),
        "properties": {k: props[k] for k in _INDEX_PROP_KEYS
                       if props.get(k) not in (None, "", [])},
    }
    if props.get("ugs:renders"):  # bound GL style → lets the map view style from the index alone
        entry["properties"]["ugs:renders"] = props["ugs:renders"]
    assets = {
        k: {kk: a[kk] for kk in ("href", "type", "roles", "title") if a.get(kk) is not None}
        for k, a in (item.get("assets") or {}).items()
    }
    if assets:
        entry["assets"] = assets
    # Item docs live at `<collection>/<id>/<id>.json`. A leaf index sits inside that
    # collection dir, the rollup one level above it — so only the rollup carries the segment.
    sub = prefix if prefix is not None else f"{item['collection']}/" if rollup and item.get("collection") else ""
    self_link = {"rel": "self", "href": f"./{sub}{item['id']}/{item['id']}.json",
                 "type": "application/geo+json"}
    wlinks = [{kk: lnk[kk] for kk in ("rel", "href", "type", "pmtiles:layers") if lnk.get(kk) is not None}
              for lnk in (item.get("links") or []) if lnk.get("rel") in ("pmtiles", "cog")]
    entry["links"] = [self_link, *wlinks]
    return entry


def _index_doc(collection: str, items: list[dict], *, rollup: bool = False) -> dict:
    """`rollup` = this index spans child collections (ROLLUP_INDEX_CATALOGS), so it sits one
    directory above the items it lists and their `self` hrefs need the collection segment."""
    entries = [_index_entry(it, rollup=rollup)
               for it in sorted(items, key=lambda it: it.get("id", ""))]
    return {"type": "ugs-items-index", "collection": collection,
            "count": len(entries), "items": entries}


def _root_doc(children: list[dict]) -> dict:
    """Root catalog. `children` = [{href, title, count}, …] — each top-level child is a flat
    collection's `collection.json` or a nesting sub-catalog's `catalog.json`.

    Review catalog only: also links (rel=child) to the published production catalog, so the review
    app browses prod + review together. Prod is referenced, never copied — it stays single-sourced on
    the public CDN, and its assets are public URLs (no signing), unlike the review bucket's."""
    links = [
        {"rel": "root", "href": "./catalog.json", "type": "application/json"},
        {"rel": "self", "href": "./catalog.json", "type": "application/json"},
        *catalog_docs.markdown_links(),
        *[_child_link(c["href"], c.get("title"), c.get("count"), c.get("mappable"))
          for c in sorted(children, key=lambda c: c["href"])],
    ]
    if config.IS_REVIEW_CATALOG:
        links.append(_child_link(config.PUBLIC_CATALOG_URL,
                                 "UGS warehouse — published (production) catalog", None))
    # Federated external catalogs (e.g. USWB) — referenced by URL, single-sourced on their
    # own CDN. Standard clients follow rel=child across the origin like any other child.
    for url, title in config.EXTERNAL_CATALOGS:
        links.append(_child_link(url, title, None))
    return {
        "type": "Catalog",
        "stac_version": STAC_VERSION,
        "stac_extensions": [PORTOLAN_SCHEMA],
        "id": config.CATALOG_ID,
        "title": config.CATALOG_TITLE,
        "description": ROOT_DESCRIPTION,
        "links": links,
    }


def _write_markdown(path: str, *, title: str, description: str, kind: str, children: int,
                   extent: dict | None = None, items: list[dict] | None = None,
                   service: bool = False) -> None:
    """Write README.md + AGENTS.md beside a catalog.json or collection.json.

    Both are regenerated on every refresh from the same values as the JSON, so the prose cannot
    drift from the metadata it describes. `path` is the layout path, empty for the root. `service`
    marks a node whose items are served live by OGC API Features — the AGENTS.md then names that
    query endpoint instead of denying one exists (see `catalog_docs.agents`).
    """
    prefix = f"{config.STAC_PREFIX}/{path}" if path else config.STAC_PREFIX
    for name, body in (
        (catalog_docs.README_NAME, catalog_docs.readme(
            title=title, description=description, kind=kind, children=children,
            extent=extent, items=items)),
        (catalog_docs.AGENTS_NAME, catalog_docs.agents(
            title=title, kind=kind, path=path, children=children, items=items, service=service)),
    ):
        gcs.put_bytes(body.encode(), f"{prefix}/{name}",
                      content_type=catalog_docs.MARKDOWN_MIME, cache_control=gcs.CACHE_CATALOG)


def _write_json(doc: dict, object_path: str) -> None:
    # catalog.json / collection.json / items.json — short edge cache + SWR (see gcs.CACHE_CATALOG),
    # stored gzipped: the biggest of these (a 4,212-item index) is 6.3 MB plain, 250 KB compressed.
    gcs.put_bytes(json.dumps(doc, indent=2).encode(), object_path,
                  content_type="application/json", cache_control=gcs.CACHE_CATALOG,
                  compress=True)


# Publication-series descriptions, verbatim from geology.utah.gov/map-pub. Set as the series
# collection's STAC `description` (any client renders it; the viewer already shows it).
SERIES_DESC = {
    "B": "Bulletins are topically and/or geographically comprehensive — mostly original work or a comprehensive synthesis of existing data.",
    "C": "Circulars address timely subjects, have a limited shelf life, and are geared toward a wide, non-specialized audience.",
    "DS": "Data Series — datasets, databases, and accompanying documents; a repository for information gathered in support of UGS projects.",
    "M": "The Map series includes original and compiled geologic quadrangle maps, economic-resource maps, and groundwater recharge/discharge maps.",
    "MP": "Miscellaneous Publications — the principal series available to non-UGS authors; substantive works that need not conform to UGS format standards.",
    "OFR": "Open-File Reports are documents intended to stand temporarily or permanently with minimal technical review and editing.",
    "PI": "Public Information Series — brief topical reports or brochures making nontechnical geologic information available to the public.",
    "RI": "Reports of Investigation present site- or project-specific investigations by UGS staff; generally of limited scope and/or duration.",
    "SS": "Special Studies are substantive scientific works (like Bulletins), but with more restricted subject matter.",
}

# Serving-topic groups carry NO authored title or description. The group is the dbt mart schema,
# so its only honest label is the schema name itself (prettified, the same dumb transform items
# get). A curated name for `emp` or `gengis` would be invention — when upstream publishes one
# (raw.schema_registry), inherit it here; until then the catalog says what it knows. Pub series
# differ — SERIES_DESC is verbatim UGS copy from geology.utah.gov/map-pub, inherited, not written.

# Nesting catalogs that ALSO publish a rollup items.json spanning every child collection. Keeps
# one-URL consumers (the tiles service, the ops console) working across a split
# without walking N sub-collections. Deliberately NOT pubs: thousands of items in one document.
ROLLUP_INDEX_CATALOGS = {SERVING_TOPICS_CATALOG}

# Catalogs whose children are publication SERIES — the only place a group's title is the items'
# `ugs:pub_type`, because there the collection IS the pub type ("Open File Report" = the OFR series).
# Anywhere else that field describes the source publication, not the group: a raster mosaic built
# from OFR plates was titled "Open File Report" instead of naming the layer (#86).
PUB_SERIES_CATALOGS = {"ugs-publications", "ugs-mining-district-files", "ugs-external"}

# Item property an ingest can send to name its own collection ("24k Geologic Map Series" for the
# geolmap_24k_series rasters). Inherited, not authored here — absent it, the group falls back to
# the prettified collection id, never to a field that means something else.
COLLECTION_TITLE_PROP = "ugs:collection_title"


def _group_title(catalog: str, items: list[dict]) -> str | None:
    """Title for a nested collection, inherited from its items — an explicit
    `ugs:collection_title` first, then the pub type for a publication series, else None
    (`_collection_doc` prettifies the id)."""
    for it in items:
        title = (it.get("properties") or {}).get(COLLECTION_TITLE_PROP)
        if title:
            return title
    if catalog in PUB_SERIES_CATALOGS:
        return next((it.get("properties", {}).get("ugs:pub_type") for it in items
                     if it.get("properties", {}).get("ugs:pub_type")), None)
    return None


def refresh_catalog() -> None:
    """Rebuild the root catalog + every collection.json by listing items in GCS.

    Derive-from-truth + idempotent: safe to run after each ingest or on demand. Under
    concurrent ingests the last writer wins (brief staleness, self-heals next run).
    """
    from concurrent.futures import ThreadPoolExecutor

    groups = _group_items(gcs.list_paths(config.STAC_PREFIX))

    # 1. Write each leaf collection.json + items.json (flat or nested). collection id = the
    #    path's last segment (a series code when nested); title from the items' pub type.
    #    Record per-path {id, title, count} so the hierarchy links can carry counts.
    leaf: dict[str, dict] = {}
    rollup: dict[str, list[dict]] = {}   # nesting catalog -> its children's items (see ROLLUP_INDEX_CATALOGS)
    everything: list[dict] = []          # every item's index entry, for the root items.json
    served: list[dict] = []              # the items the OGC API Features service serves
    with ThreadPoolExecutor(max_workers=64) as executor:
        for path, item_ids in groups.items():
            def _fetch_one(iid: str) -> dict | None:
                try:
                    return json.loads(gcs.get_bytes(item_object_path(path, iid)).decode())
                except FileNotFoundError:
                    return None  # listed-then-deleted between the list and this read — legitimately silent
                except Exception as e:  # noqa: BLE001 — one bad item shouldn't sink the refresh, but say so
                    # An item dropping out of items.json is silent data loss — this is how #341 stayed
                    # invisible for weeks. Log it so the next obstore/GCS quirk doesn't repeat that with
                    # zero lines in the log.
                    print(f"[catalog] dropped {path}/{iid} from the index: "
                          f"{type(e).__name__}: {(str(e).splitlines() or [''])[0]}", file=sys.stderr)
                    return None

            items = [it for it in executor.map(_fetch_one, sorted(item_ids)) if it is not None]
            if has_feature_service(path):
                served.extend(items)
            nested = "/" in path
            top, cid = path.split("/")[0], path.split("/")[-1]
            # Title/description are inherited, never authored here: see _group_title. A group with
            # nothing to inherit stays None and _collection_doc falls back to prettify(id).
            title = _group_title(top, items) if nested else None
            mappable = sum(1 for it in items if _is_mappable(it))
            # Series blurbs are keyed by short codes (M, C, B…) — only look them up under a pub
            # catalog, or a raster layer that happened to be named `M` would inherit the Map series text.
            desc = SERIES_DESC.get(cid) if top in PUB_SERIES_CATALOGS else None
            if nested and top in ROLLUP_INDEX_CATALOGS:
                rollup.setdefault(top, []).extend(items)
            # From the fetched docs, so an item that failed to fetch just carries no title.
            item_titles = {it["id"]: it["properties"]["title"] for it in items
                           if it.get("id") and it.get("properties", {}).get("title")}
            # The mirror is derived from these same items, so it is rebuilt whenever the
            # collection is — the two cannot drift.
            mirror = item_mirror.write(path, items)
            _write_json(_collection_doc(cid, path, item_ids, _extent(items), title=title,
                                        mappable=mappable, description=desc,
                                        item_titles=item_titles,
                                        assets=_collection_assets(path, items, mirror)),
                        f"{config.STAC_PREFIX}/{path}/collection.json")
            _write_json(_index_doc(cid, items), f"{config.STAC_PREFIX}/{path}/items.json")
            everything.extend(_index_entry(it, prefix=f"{path}/") for it in items)
            _write_markdown(path, title=title or prettify(cid),
                            description=desc or f"UGS warehouse — {title or cid}.",
                            kind="collection", children=len(item_ids),
                            extent=_extent(items), items=items,
                            service=has_feature_service(path))
            leaf[path] = {"id": cid, "title": title or prettify(cid), "count": len(item_ids),
                          "mappable": mappable}

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
            rolled = rollup.get(top)
            if rolled is not None:  # one index spanning every child collection
                _write_json(_index_doc(top, rolled, rollup=True), f"{config.STAC_PREFIX}/{top}/items.json")
            _write_json(_subcatalog_doc(top, kids, title=ptitle, items_index=rolled is not None),
                        f"{config.STAC_PREFIX}/{top}/catalog.json")
            _write_markdown(top, title=ptitle,
                            description=f"UGS warehouse — {ptitle}, by data series.",
                            kind="catalog", children=len(kids), items=rolled,
                            # The serving-topics sub-catalog's children are all featureserv-served;
                            # its AGENTS.md should name the query endpoint too (pubs series aren't).
                            service=top == SERVING_TOPICS_CATALOG)
            root_children.append({"href": f"./{top}/catalog.json", "title": ptitle,
                                  "count": sum(k["count"] for k in kids),
                                  "mappable": sum(k["mappable"] for k in kids)})
        else:                  # flat collection
            root_children.append({"href": f"./{top}/collection.json",
                                  "title": leaf[top]["title"], "count": leaf[top]["count"],
                                  "mappable": leaf[top]["mappable"]})
    _write_json(_root_doc(root_children), f"{config.STAC_PREFIX}/catalog.json")
    # Every item in one index, so a client that wants the whole catalog makes one request.
    _write_json({"type": "ugs-items-index", "collection": None, "count": len(everything),
                 "items": sorted(everything, key=lambda e: e["links"][0]["href"])},
                f"{config.STAC_PREFIX}/items.json")
    _write_markdown("", title=config.CATALOG_TITLE, description=ROOT_DESCRIPTION,
                    kind="catalog", children=len(root_children))
    feature_service.write(served)

    n = sum(len(v) for v in groups.values())
    print(f"[catalog] {config.public_url(config.STAC_PREFIX + '/catalog.json')} "
          f"({len(groups)} collections, {n} items)")
