"""Shared STAC — item builder, collections hierarchy, derive-from-truth catalog refresh.

Both producers emit items through `build_item` + `write_item`, into ONE catalog laid out
with collections:

    {STAC_PREFIX}/catalog.json                     root  -> child collections
    {STAC_PREFIX}/{collection}/collection.json      coll  -> items
    {STAC_PREFIX}/{collection}/{id}/{id}.json        item

A collection may nest one level — a sub-catalog whose children are collections
(`ugs-serving-topics/<SCHEMA>`). Nesting sub-catalogs listed in `ROLLUP_INDEX_CATALOGS` also
publish an items.json spanning every child, so one-fetch consumers keep a single URL for "all of X".

Publications nest two levels: `<group>/catalog.json` -> series `<group>/<SERIES>/catalog.json`
-> publication `<group>/<SERIES>/<SID>/collection.json` -> items `<SID>/<ITEM_ID>/<ITEM_ID>.json`.
The pubs ingest writes the publication collections (`build_collection` + `write_collection`). A
series not yet fully reingested stays in the old layout: one `<group>/<SERIES>/collection.json`
over `<SID>/<SID>.json` items.

`refresh_catalog()` lists GCS and rebuilds the root + every catalog and non-publication
collection.json from what's actually there (idempotent, concurrency-tolerant), no manual regen,
no shared-file races.
"""
from __future__ import annotations

import json
import re
import sys
from typing import NamedTuple

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
SCI_EXT = "https://stac-extensions.github.io/scientific/v1.0.0/schema.json"
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


# A serving-topic layer is also published as its own collection, beside its item, so the catalog
# can move to Portolan's one-dataset-per-collection grain without breaking a consumer (#257). Index
# entries point at it under this rel.
LAYER_COLLECTION_REL = "ugs-layer-collection"


def has_layer_collections(collection_path: str) -> bool:
    """True for a serving-topic schema collection, whose items each get a layer collection."""
    return collection_path.startswith(f"{SERVING_TOPICS_CATALOG}/")


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


def pmtiles_asset(href: str) -> dict:
    """The `pmtiles` asset beside a vector layer's `pmtiles_link`: the same archive as a download."""
    return {"href": href, "type": config.PMTILES_MIME, "roles": ["visual"],
            "title": "PMTiles vector tiles", "description": USAGE_PMTILES}


def topic_thumbnail_asset(stem: str) -> dict:
    """A serving topic's styled-preview asset. The thumbs job and the vector ingest both stamp it,
    so both build it here."""
    return {"href": config.public_url(config.topic_thumbnail_path(stem)), "type": config.WEBP_MIME,
            "roles": ["thumbnail"], "title": "Styled preview", "description": USAGE_THUMBNAIL}


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
    """Add `file:size`/`file:checksum` to our-bucket assets that lack a checksum (mutates item).

    `index` comes from `gcs.list_file_meta`; without one, each asset is looked up on its own.
    """
    missing = {k: p for k, a in (item.get("assets") or {}).items()
               if "file:checksum" not in a and (p := object_path_of(a.get("href", "")))}
    if index is None:
        index = {p: m for p in set(missing.values()) if (m := gcs.get_file_meta(p)) is not None}
    n = 0
    for key, path in missing.items():
        asset, fields = item["assets"][key], file_fields(index.get(path))
        if fields and ("file:checksum" in fields or "file:size" not in asset):
            asset.update(fields)
            n += 1
    if n and FILE_EXT not in (exts := item.setdefault("stac_extensions", [])):
        exts.append(FILE_EXT)
    return n


def _declare_proj_table(exts: list[str], fields: dict) -> list[str]:
    """`exts` plus the projection and table extensions when `fields` (an Item's properties or a
    Collection's top-level members) carry a `proj:*` or `table:*` member, so a producer never
    declares them by hand."""
    if any(k.startswith("proj:") for k in fields) and PROJ_EXT not in exts:
        exts.append(PROJ_EXT)
    if any(k.startswith("table:") for k in fields) and TABLE_EXT not in exts:
        exts.append(TABLE_EXT)
    return exts


def build_item(*, item_id: str, collection: str, geometry: dict | None,
               bbox: list[float] | None, datetime_iso: str | None,
               properties: dict, assets: dict,
               extra_links: list[dict] | None = None,
               stac_extensions: list[str] | None = None,
               proj_epsg: int | None = None,
               collection_path: str | None = None) -> dict:
    """A STAC Item placed in the collection-nested layout.

    Lives at `{collection_path}/{id}/{id}.json` (default `collection_path` = `collection`,
    the flat one-level layout; pubs pass their publication's `<group>/<SERIES>/<SID>` path). The
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
    exts = _declare_proj_table(list(stac_extensions or []), props)
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
    segment for flat collections, `ugs-serving-topics/<SCHEMA>` for a nested one, or
    `<group>/<SERIES>/<SID>` for a publication."""
    return f"{config.STAC_PREFIX}/{collection_path}/{item_id}/{item_id}.json"


def related_link(collection_path: str, item_id: str, title: str) -> dict:
    """A STAC `related` link to an item, by its absolute CDN href (no relative-depth math). Both
    producers build their relationship links here, so the shapes cannot drift."""
    return {"rel": "related", "href": config.public_url(item_object_path(collection_path, item_id)),
            "type": "application/geo+json", "title": title}


def foreign_key(fields: list[str], resource: str, href: str, ref_fields: list[str],
                **extra) -> dict:
    """A Frictionless Table Schema foreignKey (`ugs:foreign_keys` entry): the asset's `fields`
    reference `ref_fields` of `resource`, whose file is `href`. `resource` alone is a bare name
    Frictionless resolves inside a datapackage, which this catalog has none of, so `href` names the
    file. `extra` adds UGS fields such as `ugs:detected_by`. Both producers build their foreign keys
    here."""
    return {"fields": list(fields),
            "reference": {"resource": resource, "href": href, "fields": list(ref_fields)}, **extra}


def collection_object_path(collection_path: str) -> str:
    """GCS path of the collection.json at `collection_path` (e.g. a publication's
    `ugs-publications/OFR/OFR-1`)."""
    return f"{config.STAC_PREFIX}/{collection_path}/collection.json"


def _collection_skeleton(*, collection_id: str, path: str, title: str, description: str,
                         extent: dict, stac_extensions: list[str], fields: dict, assets: dict | None,
                         head_links: list[dict] | None = None,
                         tail_links: list[dict] | None = None) -> dict:
    """The parts every collection.json shares, in one order: core fields, then `fields`, then
    `assets` when there are any; links root/parent/self (depth from `path`), `head_links`, license,
    the README/AGENTS links, then `tail_links`. License is CC-BY-4.0 by UGRC policy, with UGS as
    producer, licensor and host."""
    depth = path.count("/") + 1
    return {
        "type": "Collection",
        "stac_version": STAC_VERSION,
        "stac_extensions": stac_extensions,
        "id": collection_id,
        "title": title,
        "description": description,
        "license": config.DATA_LICENSE,
        "providers": config.PROVIDERS,
        "extent": extent,
        **fields,
        # A collection with nothing to carry omits the key rather than publish an empty object.
        **({"assets": assets} if assets else {}),
        "links": [
            {"rel": "root", "href": "../" * depth + "catalog.json", "type": "application/json"},
            {"rel": "parent", "href": "../catalog.json", "type": "application/json"},
            {"rel": "self", "href": "./collection.json", "type": "application/json"},
            *(head_links or []),
            {"rel": "license", "href": config.LICENSE_URL, "type": "text/html", "title": config.DATA_LICENSE},
            # Portolan requires README.md and AGENTS.md beside every node, linked from the JSON.
            *catalog_docs.markdown_links(),
            *(tail_links or []),
        ],
    }


def build_collection(*, collection_id: str, collection_path: str, title: str, description: str,
                     extent: dict, assets: dict, item_titles: dict[str, str],
                     extra_links: list[dict] | None = None, fields: dict | None = None,
                     stac_extensions: list[str] | None = None,
                     item_link_fields: dict[str, dict] | None = None) -> dict:
    """A leaf Collection at `{collection_path}/collection.json` whose items sit at
    `./{id}/{id}.json`. Pure: the caller supplies the extent, assets and item titles. `fields` are
    extra top-level members (`ugs:*`, `keywords`, `version`/`deprecated`). `item_link_fields` adds
    fields to an item's rel=item link, so a client can group items without fetching them. An empty
    `item_titles` is a single-file collection: its data are the collection-level assets."""
    fields = dict(fields or {})
    exts = _declare_proj_table([PORTOLAN_SCHEMA, *(stac_extensions or [])], fields)
    if ("version" in fields or "deprecated" in fields) and VERSION_EXT not in exts:
        exts.append(VERSION_EXT)
    doc = _collection_skeleton(
        collection_id=collection_id, path=collection_path, title=title, description=description,
        extent=extent, stac_extensions=exts, fields=fields, assets=assets,
        tail_links=[*(extra_links or []),
                    *[{"rel": "item", "href": f"./{i}/{i}.json", "type": "application/geo+json",
                       "title": item_titles[i] or i, **(item_link_fields or {}).get(i, {})}
                      for i in sorted(item_titles)]])
    doc["_collection_path"] = collection_path
    return doc


def _arrow_duck_name(t) -> str | None:
    """The DuckDB type name a pyarrow type reads back as, or None for a type with no scalar
    equivalent (nested, binary, time, duration). A dictionary column reads as its value type."""
    import pyarrow as pa

    if pa.types.is_dictionary(t):
        return _arrow_duck_name(t.value_type)
    for check, name in ((pa.types.is_integer, "BIGINT"), (pa.types.is_floating, "DOUBLE"),
                        (pa.types.is_decimal, "DECIMAL"), (pa.types.is_boolean, "BOOLEAN"),
                        (pa.types.is_timestamp, "TIMESTAMP"), (pa.types.is_date, "DATE"),
                        (pa.types.is_string, "VARCHAR"), (pa.types.is_large_string, "VARCHAR")):
        if check(t):
            return name
    return None


def table_type(col_type) -> str:
    """STAC Table extension type name for a DuckDB type string or a pyarrow type, so a column reads
    the same in a serving topic and in a publication layer. An unmapped type keeps its own name."""
    if not isinstance(col_type, str):
        duck = _arrow_duck_name(col_type)
        if duck is None:
            return str(col_type)
        col_type = duck
    t = col_type.upper()
    if t.startswith("GEOMETRY"):
        return "geometry"
    if "INT" in t or t == "HUGEINT":
        return "integer"
    if t in ("DOUBLE", "FLOAT", "REAL") or t.startswith("DECIMAL"):
        return "number"
    if t in ("BOOLEAN", "BOOL"):
        return "boolean"
    if t.startswith("TIMESTAMP"):
        return "datetime"
    if t == "DATE":
        return "date"
    if "CHAR" in t or t in ("VARCHAR", "TEXT", "STRING"):
        return "string"
    return col_type.lower()


def override_object(item_id: str) -> str:
    """GCS path of an item's hand-authored metadata override (keyed by the exact STAC item id)."""
    return f"{config.OVERRIDES_PREFIX}/{item_id}.json"


def patch_published(object_path: str, updates: dict, *, target: str = "properties") -> bool:
    """Merge `updates` into a published Item's `properties` (`target="properties"`) or a published
    Collection's top-level fields (`target="fields"`) in place: an instant edit that shows without a
    reingest (the durable source stays the override sidecar; a later reingest reapplies it). A
    collection's README.md/AGENTS.md are re-rendered to match.

    The write is conditional on the version read; a concurrent write is retried once on the new
    version, then raises `gcs.Changed`. Returns False when nothing is published at `object_path`;
    any other read or write error raises. Not gzipped, as `write_item`: a gzipped item breaks every
    obstore `get_bytes` read.
    """
    if target not in ("properties", "fields"):
        raise ValueError(f"unknown patch target {target!r}")
    for attempt in (1, 2):
        try:
            raw, version = gcs.get_bytes_versioned(object_path)
        except FileNotFoundError:
            return False
        doc = json.loads(raw)
        if target == "properties":
            doc.setdefault("properties", {}).update(updates)
        else:
            doc.update(updates)
        try:
            # Same cache policy as write_item, so an edited document keeps a consistent header.
            gcs.put_bytes_if_unchanged(
                json.dumps(doc, indent=2).encode(), object_path, version,
                content_type="application/geo+json" if target == "properties" else "application/json",
                cache_control=gcs.CACHE_CATALOG)
            break
        except gcs.Changed:
            if attempt == 2:
                raise
    if target == "fields":
        path = object_path.removeprefix(f"{config.STAC_PREFIX}/").removesuffix("/collection.json")
        _write_markdown(path, title=doc.get("title") or prettify(doc.get("id", path)),
                        description=doc.get("description") or "", kind="collection",
                        children=sum(1 for lk in doc.get("links") or [] if lk.get("rel") == "item"),
                        extent=doc.get("extent"))
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
    """Value of `prop` in the currently-published item.json, or None if the item doesn't exist yet.
    Lets a reingest PRESERVE a field (e.g. description) when the incoming metadata omits it:
    last-non-empty wins, so a resubmit with no description keeps the old one instead of blanking it.
    Any other read error raises: falling back on a transient failure would publish, and then
    preserve, the wrong value."""
    try:
        item = json.loads(gcs.get_bytes(item_object_path(collection_path, item_id)))
    except FileNotFoundError:
        return None
    return (item.get("properties") or {}).get(prop)


def prior_collection_field(collection_path: str, field: str):
    """A field of the published collection.json at `collection_path`, or None when there is none
    yet. Lets a reingest keep a value the source no longer carries (preserve-on-empty)."""
    try:
        return json.loads(gcs.get_bytes(collection_object_path(collection_path)).decode()).get(field)
    except FileNotFoundError:
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


def write_collection(coll: dict, *, children: int) -> str:
    """Write a leaf collection.json plus the README.md/AGENTS.md Portolan requires beside it.
    `children` is its item count, for the prose."""
    path = coll["_collection_path"]
    doc = {k: v for k, v in coll.items() if k != "_collection_path"}
    obj = collection_object_path(path)
    # plain, like items: obstore can't read a gzipped GCS object (no Content-Length)
    _write_json(doc, obj, compress=False)
    _write_markdown(path, title=doc["title"], description=doc["description"], kind="collection",
                    children=children, extent=doc["extent"])
    return obj


def update_collection(doc: dict, collection_path: str) -> str:
    """Rewrite a leaf collection.json read back from GCS, leaving the README.md/AGENTS.md beside it
    as they are (`write_collection` rewrites those from the extent and item count)."""
    obj = collection_object_path(collection_path)
    _write_json({k: v for k, v in doc.items() if k != "_collection_path"}, obj, compress=False)
    return obj


PLATE_SUFFIX = "_plate"
# Everything outside RFC 3986's unreserved set needs escaping in a path segment. See `item_id_for`.
_UNSAFE_IN_PATH = re.compile(r"[^A-Za-z0-9._~-]+")
# A publication id never holds `__`, so the first `__` in a layer's id (`<pub id>__<label>`)
# always ends the publication id.
_DOUBLE_UNDERSCORE = re.compile(r"_{2,}")


def item_id_for(sid: str) -> str:
    """A publication's published id: its series id, made safe to sit in a URI path.

    The id is also a directory name and the last segment of every link that reaches the item, so a
    space in it (`Geologic map of Utah`, `557.92 UT1CO`) would be a raw space in links a strict
    client rejects. The exact source string stays on the item as `ugs:series_id`."""
    return _DOUBLE_UNDERSCORE.sub("_", _UNSAFE_IN_PATH.sub("-", (sid or "").strip()))


def plate_item_id(sid: str) -> str:
    return f"{item_id_for(sid)}{PLATE_SUFFIX}"


# Property (a Collection's top-level member) naming a generated MapLibre style file for the doc's
# PMTiles. It points at the file; the render binding is made by `attach_renders`.
GENERATED_STYLE = "ugs:generated_style"


def doc_fields(doc: dict) -> dict:
    """Where a STAC doc keeps its `ugs:*` fields: the Collection itself, or an Item's properties."""
    return doc if doc.get("type") == "Collection" else doc.setdefault("properties", {})


def renders_for_item(item: dict, asset_keys: set[str] | None = None) -> tuple[dict, dict | None]:
    """`styles.renders_for` for a STAC item or Collection. A publication's plate Item (`<SID>_plate`)
    with no style of its own uses the style keyed by its publication's id. Layer Items match only
    their own id. With no ugs-styles style at all, a doc that names a generated style
    (`GENERATED_STYLE`) uses that: an ugs-styles style always wins."""
    keys = set((item.get("assets") or {}).keys()) if asset_keys is None else asset_keys
    result = styles.renders_for(item["id"], keys)
    base = plate_base_id(item)
    if not result[0] and base is not None:
        result = styles.renders_for(base, keys)
    generated = doc_fields(item).get(GENERATED_STYLE)
    if not result[0] and isinstance(generated, str):
        result = styles.generated_renders(generated, keys)
    return result


def plate_base_id(item: dict) -> str | None:
    """The publication id a plate Item's style falls back to, or None when `item` is not its
    publication's plate. A layer whose id also ends in `_plate` is not the plate."""
    sid = (item.get("properties") or {}).get("ugs:series_id")
    if not isinstance(sid, str) or item.get("id") != plate_item_id(sid):
        return None
    return item["id"][: -len(PLATE_SUFFIX)]


def attach_renders(item: dict) -> None:
    """Attach a `ugs:renders` block (+ a vector `style` asset) by looking the item id up in the
    ugs-styles manifest. No-op when nothing matches (mutates item in place).

    Call before `write_item` (or `write_collection`, for a single-file publication's Collection, which
    carries the block as a top-level member). Best-effort: styling never blocks an ingest (see
    `core.styles`).

    NOT the STAC render extension: that extension is raster/titiler-oriented, and its v2.0.0 schema
    *requires* a `rel:"render"` image link whenever web-map-links is also present — but we render
    vector layers client-side from a MapLibre GL `style_url`, so we have no render-image endpoint.
    `ugs:renders` is the UGS-prefixed equivalent (identical shape): spec-legal, no schema to fail.
    The standard `roles:["style"]` `style` asset is the interoperable pointer to the GL style.
    """
    renders, style_asset = renders_for_item(item)
    if not renders:
        return
    doc_fields(item)["ugs:renders"] = renders
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
    segment for flat collections (`ugs-serving-topics`), two for nested series
    (`ugs-serving-topics/<topic>`), or three for a publication (`ugs-publications/<SERIES>/<SID>`;
    the two-segment publication form is an old-layout series). catalog.json / collection.json /
    items.json are skipped.
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


def union_bbox(boxes: list[list[float]]) -> list[float] | None:
    """The [W, S, E, N] box covering every 2D box in `boxes`, or None when there are none."""
    if not boxes:
        return None
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]


def _extent(items: list[dict]) -> dict:
    """Real spatial + temporal extent from the collection's items (union bbox, min/max
    datetime). Falls back to the Utah bbox if no item bboxes are present."""
    bxs = [to_2d_bbox(it["bbox"]) for it in items if it.get("bbox") and len(it["bbox"]) in (4, 6)]
    dts = sorted(it["properties"]["datetime"] for it in items
                 if it.get("properties", {}).get("datetime"))
    bbox = union_bbox(bxs) or UTAH_BBOX
    interval = [[dts[0], dts[-1]]] if dts else [[None, None]]
    return {"spatial": {"bbox": [bbox]}, "temporal": {"interval": interval}}


def _collection_doc(collection: str, path: str, item_ids: list[str],
                    extent: dict | None = None, *, title: str | None = None,
                    service: bool | None = None, mappable: int | None = None,
                    description: str | None = None,
                    item_titles: dict[str, str] | None = None,
                    assets: dict | None = None, layers: bool = False) -> dict:
    """A collection.json at `{path}/collection.json`. `collection` is its STAC id (a series
    code like `DS` when nested, else the path). Root/parent links climb out per path depth;
    the OGC API Features link is added only for flat collections (serving topics — nested
    pub series aren't in featureserv) unless `service` is set explicitly."""
    if service is None:
        # Flat collections, plus the nested serving-topic schemas — those are precisely the
        # collections whose items featureserv binds, and they'd otherwise be the only ones with
        # no pointer to the service that serves them.
        service = has_feature_service(path)
    return _collection_skeleton(
        collection_id=collection, path=path, title=title or prettify(collection),
        description=description or f"UGS warehouse — {title or collection}.",
        extent=extent or {"spatial": {"bbox": [UTAH_BBOX]}, "temporal": {"interval": [[None, None]]}},
        stac_extensions=[PORTOLAN_SCHEMA],
        # Counts are UGS-prefixed top-level extras, NOT `summaries` — STAC summaries values must be
        # arrays/ranges/JSON-Schema (they summarize item *property* ranges), so a scalar count there
        # fails strict validation. A prefixed top-level field is spec-legal (additionalProperties).
        fields={"ugs:item_count": len(item_ids),
                **({"ugs:mappable_count": mappable} if mappable is not None else {})},
        assets=assets,
        # Compact items index — one fetch for the whole list (viewers read this instead of N
        # item.json fetches; the per-item docs stay the source of truth for detail). NOT
        # rel:"items": STAC API reserves that for an ItemCollection endpoint, and a standard client
        # (STAC Browser) that follows it here fails the whole collection with "not a valid list of
        # STAC Items". Those clients use the rel:"item" links below.
        head_links=[{"rel": INDEX_REL, "href": "./items.json", "type": "application/json",
                     "title": "Items index"}],
        tail_links=[
            # Service ROOT, not `/collections/{collection}`: featureserv names its collections after
            # STAC *item* ids (`hazards_qfaults`), so a per-collection path never existed on any
            # host. The queryable per-layer link lives on the item instead (vector.sink_stac).
            *([{"rel": "service", "href": f"{PGF_BASE_URL}/collections", "type": "application/json", "title": "OGC API Features service"}] if service else []),
            # Titled, so listing a collection needs no fetch per item; Portolan requires one.
            *[{"rel": "item", "href": f"./{i}/{i}.json", "type": "application/geo+json",
               "title": (item_titles or {}).get(i) or i}
              for i in sorted(item_ids)],
            # The same layers as collections (see LAYER_COLLECTION_REL).
            *([{"rel": "child", "href": f"./{i}/collection.json", "type": "application/json",
                "title": (item_titles or {}).get(i) or i} for i in sorted(item_ids)] if layers else []),
        ])


def _layer_collection_doc(item: dict, path: str) -> dict:
    """A serving-topic layer as its own collection, at `{path}/{id}/collection.json`.

    Portolan's grain: one dataset per collection, its data, preview and style as collection-level
    assets, and no items. Derived from the layer's item, so the two cannot disagree.
    """
    props = item.get("properties") or {}
    assets = item.get("assets") or {}
    title = props.get("title") or prettify(item["id"])
    exts = [PORTOLAN_SCHEMA, WEB_MAP_LINKS_EXT]
    if any("table:columns" in a for a in assets.values()):
        exts.append(TABLE_EXT)
    if any("file:size" in a or "file:checksum" in a for a in assets.values()):
        exts.append(FILE_EXT)
    depth = path.count("/") + 2
    return {
        "type": "Collection",
        "stac_version": STAC_VERSION,
        "stac_extensions": exts,
        "id": item["id"],
        "title": title,
        "description": props.get("description") or f"UGS warehouse — {title}.",
        **({"keywords": props["keywords"]} if props.get("keywords") else {}),
        "license": config.DATA_LICENSE,
        "providers": config.PROVIDERS,
        "extent": _extent([item]),
        # Our own fields ride along unchanged; standard clients ignore them.
        **{k: v for k, v in props.items() if k.startswith("ugs:")},
        "assets": assets,
        "links": [
            {"rel": "root", "href": "../" * depth + "catalog.json", "type": "application/json"},
            {"rel": "parent", "href": "../collection.json", "type": "application/json"},
            {"rel": "self", "href": "./collection.json", "type": "application/json"},
            {"rel": "license", "href": config.LICENSE_URL, "type": "text/html", "title": config.DATA_LICENSE},
            *catalog_docs.markdown_links(),
            # pmtiles, service and related links describe the layer, not where the item sits.
            *[lnk for lnk in item.get("links") or []
              if lnk.get("rel") not in ("self", "root", "parent", "collection", "license")],
        ],
    }


def _collection_assets(path: str, items: list[dict], mirror: object | None = None) -> dict:
    """Collection-level assets derived from the collection's own items.

    Portolan requires a thumbnail on a geospatial collection (PTL-VIZ-001), and a collection whose
    items are all one kind of thing can borrow an item's: the scenes of a raster dataset, or the
    publications of one old-layout series. Not a serving-topic collection: that is a dbt schema holding
    unrelated layers, so one layer's preview would misrepresent the other eleven. That is the grain
    question (#257), not something a thumbnail should paper over.

    The item is the most recent one that has a thumbnail, ties broken by id, so the preview tracks
    what was published last instead of whichever item happened to sort first.
    """
    mirror_asset = item_mirror.asset(path, mirror)
    top = path.split("/", 1)[0]
    if top != RASTER_CATALOG and top not in PUB_GROUPS:
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
    without fetching their items. An item or collection with no footprint (null geometry, or the
    Utah placeholder) has nowhere to draw, so it is not mappable."""
    if _no_footprint(item, item.get("properties") or item):
        return False
    return has_cog(item) or any(lnk.get("rel") in ("pmtiles", "cog") for lnk in (item.get("links") or []))


def has_cog(item: dict) -> bool:
    """True if the item (or an index entry) has a cloud-optimized GeoTIFF asset."""
    for a in (item.get("assets") or {}).values():
        t, h, roles = a.get("type") or "", a.get("href") or "", a.get("roles") or []
        if "cloud-optimized" in t or "cloud-optimized" in roles or h.endswith(".cog.tif"):
            return True
    return False


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
                    description: str | None = None, items_index: bool = False,
                    depth: int = 1) -> dict:
    """A nesting Catalog `depth` path segments below the root whose children each sit at
    `./<child id>/<file>`: per-series collections under a top catalog, series catalogs or
    old-layout series collections under a publications catalog, or publication collections under
    a series catalog. `children` = [{id, title, count, mappable?, file?}, …]; `file` defaults to
    `collection.json`.

    `items_index` adds a rollup index link spanning every child collection, see
    ROLLUP_INDEX_CATALOGS."""
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
            {"rel": "root", "href": "../" * depth + "catalog.json", "type": "application/json"},
            {"rel": "parent", "href": "../catalog.json", "type": "application/json"},
            {"rel": "self", "href": "./catalog.json", "type": "application/json"},
            *catalog_docs.markdown_links(),
            *([{"rel": INDEX_REL, "href": "./items.json", "type": "application/json",
                "title": "Items index (all child collections)"}] if items_index else []),
            *[_child_link(f"./{c['id']}/{c.get('file', 'collection.json')}", c.get("title"),
                          c.get("count"), c.get("mappable"))
              for c in sorted(children, key=lambda c: c["id"])],
        ],
    }


# Properties carried in the compact index — enough to render the list table + facets
# (title, date, series/type/topic/scale/author, keywords for search). The long
# `description`/citation is intentionally omitted; it loads with the full item on open.
_INDEX_PROP_KEYS = ("title", "datetime", "ugs:series_id", "ugs:series", "ugs:pub_type",
                    "ugs:topic", "ugs:scale", "ugs:author", "ugs:county", "ugs:dbt_schema",
                    "ugs:layer", "ugs:layer_kind", "ugs:row_count", "ugs:volume", "keywords")
# `ugs:layer_kind` values for Items whose geometry is their publication's area of interest, not a
# place on the map. The index leaves their bbox out and the viewer keeps them off the map.
NON_MAPPABLE_KINDS = ("table", "supporting")
# The asset fields an index entry keeps. Public: the topic-thumbnail job compares against them.
INDEX_ASSET_KEYS = ("href", "type", "roles", "title")
# The vector ingest's content fingerprint property (vector/fingerprint.py); it changes exactly when a
# topic's data or tiling does. Read by the ingest's skip check, the thumbnail job's content hash, and
# tiles/app.py (as a literal, from its own image) to version tile URLs, so a rename must change all.
CONTENT_HASH_PROP = "ugs:content_hash"


# `ugs:footprint_source` value the pubs producer writes when a document has no footprint and its
# extent is the statewide placeholder (pubs.sink_stac.FOOTPRINT_FALLBACK).
PLACEHOLDER_FOOTPRINT = "utah-bbox"


def _no_footprint(doc: dict, props: dict) -> bool:
    """True for a document whose geometry is null or whose extent is the Utah placeholder."""
    return ("geometry" in doc and doc["geometry"] is None) \
        or props.get("ugs:footprint_source") == PLACEHOLDER_FOOTPRINT \
        or props.get("ugs:layer_kind") in NON_MAPPABLE_KINDS


def _index_entry(item: dict, *, rollup: bool = False, prefix: str | None = None,
                 layer: bool = False) -> dict:
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
        # No bbox for an item without a footprint: a table (null geometry), or one placed on the
        # statewide placeholder. A list view would otherwise draw and badge Utah.
        **({} if _no_footprint(item, props) else {"bbox": item.get("bbox")}),
        "properties": {k: props[k] for k in _INDEX_PROP_KEYS
                       if props.get(k) not in (None, "", [])},
    }
    if props.get("ugs:renders"):  # bound GL style → lets the map view style from the index alone
        entry["properties"]["ugs:renders"] = props["ugs:renders"]
    assets = _asset_summaries(item)
    if assets:
        entry["assets"] = assets
    # Item docs live at `<collection>/<id>/<id>.json`. A leaf index sits inside that
    # collection dir, the rollup one level above it — so only the rollup carries the segment.
    sub = prefix if prefix is not None else f"{item['collection']}/" if rollup and item.get("collection") else ""
    self_link = {"rel": "self", "href": f"./{sub}{item['id']}/{item['id']}.json",
                 "type": "application/geo+json"}
    layer_link = [{"rel": LAYER_COLLECTION_REL, "href": f"./{sub}{item['id']}/collection.json",
                   "type": "application/json"}] if layer else []
    entry["links"] = [self_link, *_web_map_links(item), *layer_link]
    return entry


def _asset_summaries(doc: dict) -> dict:
    """The INDEX_ASSET_KEYS fields of each asset of an item or collection."""
    return {k: {kk: a[kk] for kk in INDEX_ASSET_KEYS if a.get(kk) is not None}
            for k, a in (doc.get("assets") or {}).items()}


def _web_map_links(doc: dict) -> list[dict]:
    return [{kk: lnk[kk] for kk in ("rel", "href", "type", "pmtiles:layers") if lnk.get(kk) is not None}
            for lnk in (doc.get("links") or []) if lnk.get("rel") in ("pmtiles", "cog")]


def _pub_item_index_entry(item: dict, coll: dict, *, prefix: str) -> dict:
    """A publication Item's root-index entry: its `_index_entry`, tagged by `_tag_pub_entry`."""
    return _tag_pub_entry(_index_entry(item, prefix=prefix), coll["id"], coll.get("title"))


def _tag_pub_entry(entry: dict, pub_id: str, title: str | None) -> dict:
    """Tag a publication Item's index entry with the publication's id and title
    (`ugs:in_publication`, `ugs:publication_title`). A list view drops tagged entries to keep one
    card per publication; a map layer list keeps them and names the publication."""
    entry["properties"]["ugs:in_publication"] = pub_id
    entry["properties"]["ugs:publication_title"] = title or pub_id
    return entry


def _collection_index_entry(coll: dict, *, prefix: str, item_count: int | None = None) -> dict:
    """An item-shaped index entry for a publication Collection, so a list view shows one card
    per publication. `prefix` is the path from the index's directory to the collection's folder
    (`<SID>/` in a series index, `<group>/<SERIES>/<SID>/` in the root one). An undated
    publication carries `ugs:date_unknown` instead of `datetime`; `ugs:item_count` counts the
    collection's Items (its `rel=item` links unless `item_count` is given)."""
    props: dict = {k: coll[k] for k in _INDEX_PROP_KEYS
                   if k not in ("title", "datetime") and coll.get(k) not in (None, "", [])}
    props["title"] = coll.get("title") or coll["id"]
    if coll.get("ugs:date_unknown"):
        props["ugs:date_unknown"] = True
    else:
        start = (((coll.get("extent") or {}).get("temporal") or {}).get("interval") or [[None]])[0][0]
        if start:
            props["datetime"] = start
    props["ugs:item_count"] = (item_count if item_count is not None
                               else sum(1 for lnk in coll.get("links") or [] if lnk.get("rel") == "item"))
    bboxes = ((coll.get("extent") or {}).get("spatial") or {}).get("bbox") or [None]
    # A Collection on the Utah placeholder extent has no footprint, so its entry carries no bbox.
    placeholder = coll.get("ugs:footprint_source") == PLACEHOLDER_FOOTPRINT
    entry: dict = {"id": coll["id"], **({} if placeholder else {"bbox": bboxes[0]}), "properties": props}
    assets = _asset_summaries(coll)
    if assets:
        entry["assets"] = assets
    entry["links"] = [{"rel": "self", "href": f"./{prefix}collection.json", "type": "application/json"},
                      *_web_map_links(coll)]
    return entry


def _index_doc(collection: str, items: list[dict], *, rollup: bool = False,
               layer: bool = False) -> dict:
    """`rollup` = this index spans child collections (ROLLUP_INDEX_CATALOGS), so it sits one
    directory above the items it lists and their `self` hrefs need the collection segment."""
    return _entries_doc(collection, [_index_entry(it, rollup=rollup, layer=layer)
                                     for it in sorted(items, key=lambda it: it.get("id", ""))])


def _entries_doc(collection: str | None, entries: list[dict]) -> dict:
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
                   service: bool = False, publications: bool = False) -> None:
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
            extent=extent, items=items, publications=publications)),
        (catalog_docs.AGENTS_NAME, catalog_docs.agents(
            title=title, kind=kind, path=path, children=children, items=items, service=service,
            publications=publications)),
    ):
        gcs.put_bytes(body.encode(), f"{prefix}/{name}",
                      content_type=catalog_docs.MARKDOWN_MIME, cache_control=gcs.CACHE_CATALOG)


def _write_json(doc: dict, object_path: str, *, compress: bool = True) -> None:
    # catalog.json / collection.json / items.json — short edge cache + SWR (see gcs.CACHE_CATALOG),
    # stored gzipped: the biggest of these (a 4,212-item index) is 6.3 MB plain, 250 KB compressed.
    gcs.put_bytes(json.dumps(doc, indent=2).encode(), object_path,
                  content_type="application/json", cache_control=gcs.CACHE_CATALOG,
                  compress=compress)


# Publication-series descriptions, verbatim from geology.utah.gov/map-pub. Set as the series
# catalog's (or old-layout series collection's) STAC `description`.
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

# The publication groups, each a Catalog of series Catalogs holding publication Collections.
PUBLICATIONS_COLLECTION = "ugs-publications"             # UGS/UGMS-authored + USGS Utah maps
MINING_DISTRICT_COLLECTION = "ugs-mining-district-files"  # MD series: archived 3rd-party mine files
EXTERNAL_COLLECTION = "ugs-external"                     # foreign publishers UGS only hosts
PUB_GROUPS = (PUBLICATIONS_COLLECTION, MINING_DISTRICT_COLLECTION, EXTERNAL_COLLECTION)


def pub_layout_of(path: str) -> tuple[str, str, str, str] | None:
    """Split a path under the publication catalogs, relative to the catalog root, into
    `(group, series, sid, rest)` for `<group>/<SERIES>/<SID>/<rest>`.

    A file directly in a series directory gives `sid == ""` and `rest` the file name. None for a
    path outside the publication groups or above series level."""
    parts = path.split("/")
    if len(parts) < 3 or parts[0] not in PUB_GROUPS or not all(parts[1:]):
        return None
    if len(parts) == 3:
        return parts[0], parts[1], "", parts[2]
    return parts[0], parts[1], parts[2], "/".join(parts[3:])

# Item property an ingest can send to name its own collection ("24k Geologic Map Series" for the
# geolmap_24k_series rasters). Inherited, not authored here — absent it, the group falls back to
# the prettified collection id, never to a field that means something else.
COLLECTION_TITLE_PROP = "ugs:collection_title"


def _group_title(catalog: str, items: list[dict]) -> str | None:
    """Title for a nested collection, inherited from its items — an explicit
    `ugs:collection_title` first, then the pub type for an old-layout publication series, else None
    (`_collection_doc` prettifies the id)."""
    for it in items:
        title = (it.get("properties") or {}).get(COLLECTION_TITLE_PROP)
        if title:
            return title
    if catalog in PUB_GROUPS:
        return next((it.get("properties", {}).get("ugs:pub_type") for it in items
                     if it.get("properties", {}).get("ugs:pub_type")), None)
    return None


def _is_pub_collection_path(path: str) -> bool:
    """True for a `<group>/<SERIES>/<SID>` path under a publication catalog."""
    return path.split("/")[0] in PUB_GROUPS and path.count("/") == 2


def _pub_collection_paths(paths: list[str]) -> dict[str, int]:
    """{`<group>/<SERIES>/<SID>`: 0} for every publication collection.json under a pub catalog,
    including collections with no items. The value becomes the item count during refresh."""
    out: dict[str, int] = {}
    for path in paths:
        layout = pub_layout_of(path[len(config.STAC_PREFIX):].lstrip("/"))
        if layout and layout[2] and layout[3] == "collection.json":
            out["/".join(layout[:3])] = 0
    return out


def _pub_series_layout(groups: dict[str, list[str]],
                       pub_colls: dict[str, int]) -> tuple[set[str], dict[str, list[str]]]:
    """Split publication series (`<group>/<SERIES>`) into those indexed in the collection layout
    and those still pending, `{series: [SIDs with an old-layout item and no collection.json]}`.

    A series switches as a whole, once every old-layout `<SID>/<SID>.json` in it has a
    collection.json beside it, so a partial reingest never hides a published publication."""
    series = {g for g in groups if g.split("/")[0] in PUB_GROUPS and g.count("/") == 1}
    series |= {p.rsplit("/", 1)[0] for p in [*groups, *pub_colls] if _is_pub_collection_path(p)}
    migrated: set[str] = set()
    pending: dict[str, list[str]] = {}
    for s in series:
        missing = sorted(sid for sid in groups.get(s, []) if f"{s}/{sid}" not in pub_colls)
        if missing:
            pending[s] = missing
        else:
            migrated.add(s)
    return migrated, pending


def _index_defect(doc: object) -> str | None:
    """Why a publication collection.json cannot be indexed, or None: it needs an `id`, a first
    `extent.spatial.bbox` entry, and (when it has one) a non-empty first temporal interval."""
    if not isinstance(doc, dict):
        return f"not a JSON object ({type(doc).__name__})"
    if not doc.get("id"):
        return "no id"
    extent = doc.get("extent")
    extent = extent if isinstance(extent, dict) else {}
    spatial = extent.get("spatial")
    bboxes = spatial.get("bbox") if isinstance(spatial, dict) else None
    if not (isinstance(bboxes, list) and bboxes and isinstance(bboxes[0], list) and bboxes[0]):
        return "no extent.spatial.bbox[0]"
    temporal = extent.get("temporal")
    if isinstance(temporal, dict) and "interval" in temporal:
        interval = temporal["interval"]
        if not (isinstance(interval, list) and interval and isinstance(interval[0], list)
                and interval[0]):
            return "empty extent.temporal.interval"
    return None


class _PubMeta(NamedTuple):
    """What the refresh keeps of one publication's collection.json once it has been read: the
    rest (item links, assets, `ugs:contents`, the columns and WKT2 of a single-file publication)
    is dropped, so memory does not grow with every publication."""
    id: str
    title: str | None
    pub_type: str | None
    mappable: bool
    row: dict  # its index entry, with the self link relative to the catalog root


def _with_self_href(row: dict, href: str) -> dict:
    """`row` with its self link at `href`: the one part of an index entry that depends on the
    index's directory."""
    return {**row, "links": [{**row["links"][0], "href": href}, *row["links"][1:]]}


def _write_pub_series(pub_colls: dict[str, int], has_mappable_item: set[str]
                      ) -> tuple[dict[str, list[dict]], dict[str, str], dict[str, _PubMeta]]:
    """Write `<group>/<SERIES>/catalog.json` and an `items.json` of its publication entries over
    each migrated series' publication collections, with README/AGENTS beside each. Returns
    `({group: [series child entries]}, {path: problem}, {path: _PubMeta})`; a publication
    whose collection.json is unreadable or malformed for the index is left out of its series."""
    from concurrent.futures import ThreadPoolExecutor

    def _meta(path: str) -> tuple[str, _PubMeta | str]:
        # Reduced in the reading thread, so a full collection.json is dropped as soon as it is read.
        try:
            doc = json.loads(gcs.get_bytes(collection_object_path(path)).decode())
        except Exception as e:  # noqa: BLE001 (reported as a problem; the refresh carries on)
            return path, f"unreadable collection.json: {path}: {type(e).__name__}: {e}"
        defect = _index_defect(doc)
        if defect is not None:
            return path, f"malformed collection.json: {path}: {defect}"
        return path, _PubMeta(
            doc["id"], doc.get("title"), doc.get("ugs:pub_type"), _is_mappable(doc),
            _collection_index_entry(doc, prefix=f"{path}/", item_count=pub_colls[path]))

    with ThreadPoolExecutor(max_workers=64) as ex:
        docs = dict(ex.map(_meta, sorted(pub_colls)))
    skipped = {p: d for p, d in docs.items() if isinstance(d, str)}
    series: dict[str, list[str]] = {}
    for path, doc in docs.items():
        if isinstance(doc, _PubMeta):
            series.setdefault(path.rsplit("/", 1)[0], []).append(path)
    by_group: dict[str, list[dict]] = {}
    for spath, pubs in sorted(series.items()):
        group, code = spath.split("/")
        # A publication is mappable when one of its items is, or when its lone plate sits on the
        # collection itself.
        kids = [{"id": p.rsplit("/", 1)[1], "title": docs[p].title or prettify(p.rsplit("/", 1)[1]),
                 "count": pub_colls[p], "mappable": int(p in has_mappable_item or docs[p].mappable)}
                for p in pubs]
        stitle = next((docs[p].pub_type for p in pubs if docs[p].pub_type), None) or prettify(code)
        desc = SERIES_DESC.get(code) or f"UGS warehouse - {stitle}."
        _write_json(_subcatalog_doc(code, kids, title=stitle, description=desc, depth=2,
                                    items_index=True),
                    f"{config.STAC_PREFIX}/{spath}/catalog.json")
        _write_json(_entries_doc(code, sorted(
            (_with_self_href(docs[p].row, f"./{p.rsplit('/', 1)[1]}/collection.json") for p in pubs),
            key=lambda e: e["id"])),
                    f"{config.STAC_PREFIX}/{spath}/items.json")
        _write_markdown(spath, title=stitle, description=desc, kind="catalog", children=len(kids),
                        publications=True)
        by_group.setdefault(group, []).append({"id": code, "title": stitle, "file": "catalog.json",
                                               "count": sum(k["count"] for k in kids),
                                               "mappable": sum(k["mappable"] for k in kids)})
    return by_group, skipped, {p: d for p, d in docs.items() if isinstance(d, _PubMeta)}


PUB_COVERAGE_FILE = "pub-coverage.json"


def _pub_coverage(migrated: set[str], pending: dict[str, list[str]], metas: dict[str, _PubMeta],
                  pub_rows: dict[str, list[dict]]) -> dict:
    """The small summary the admin panel reads in place of the root `items.json`:
    `{"groups": {group: {series: {publications, migrated, pending, items, cog_ids}}}}`. `items`
    counts the publications' Items, plus a single-file publication whose plate COG sits on its
    Collection; `cog_ids` lists the ids of those that carry a COG. A series still in the old layout
    has `migrated` false and `pending` publications, and nothing else counted."""
    groups: dict[str, dict[str, dict]] = {}
    for spath in sorted(migrated | set(pending)):
        group, code = spath.split("/")
        groups.setdefault(group, {})[code] = {
            "publications": 0, "migrated": spath in migrated, "pending": len(pending.get(spath, [])),
            "items": 0, "cog_ids": []}
    for path, meta in sorted(metas.items()):
        group, code, _ = path.split("/")
        entry = groups[group][code]
        rows = pub_rows.get(path, [])
        entry["publications"] += 1
        entry["items"] += len(rows)
        entry["cog_ids"] += [r["id"] for r in rows if has_cog(r)]
        if has_cog(meta.row):
            entry["items"] += 1
            entry["cog_ids"].append(meta.row["id"])
    for series in groups.values():
        for entry in series.values():
            entry["cog_ids"].sort()
    return {"type": "ugs-pub-coverage", "groups": groups}


def _write_layer_collection(item: dict, path: str) -> None:
    """Write one layer's collection.json and its README.md + AGENTS.md beside its item."""
    lpath = f"{path}/{item['id']}"
    doc = _layer_collection_doc(item, path)
    _write_json(doc, f"{config.STAC_PREFIX}/{lpath}/collection.json")
    _write_markdown(lpath, title=doc["title"], description=doc["description"], kind="layer",
                    children=0, extent=doc["extent"], items=[item], service=True)


def _duplicate_item_ids(groups: dict[str, list[str]]) -> list[str]:
    """A problem for each item id held by more than one indexed collection, naming every path.
    Item ids are unique across the catalog; this is a safety net that should never fire."""
    paths: dict[str, list[str]] = {}
    for coll, ids in groups.items():
        for iid in ids:
            paths.setdefault(iid, []).append(f"{coll}/{iid}")
    return [f"duplicate item id {iid}: {', '.join(sorted(where))}"
            for iid, where in sorted(paths.items()) if len(where) > 1]


def refresh_catalog(*, strict: bool = False) -> list[str]:
    """Rebuild the root catalog + every non-publication collection.json by listing items in GCS.

    A publication series whose every publication has a collection.json is indexed in the
    collection layout: refresh writes its series catalog over the collections the pubs ingest
    wrote and indexes their items. A series still pending stays in the old layout, one
    collection.json over `<SID>/<SID>.json` items, and the new-layout objects in it are ignored.

    Returns the problems found (publication items with no collection.json, unreadable or malformed
    publication collection.json, items that failed to load, an item id held twice), each also
    printed. The root files are always written; `strict` then raises
    if there were problems.

    Derive-from-truth + idempotent: safe to run after each ingest or on demand. Under
    concurrent ingests the last writer wins (brief staleness, self-heals next run).
    """
    from concurrent.futures import ThreadPoolExecutor

    paths = gcs.list_paths(config.STAC_PREFIX)
    groups = _group_items(paths)
    pub_colls = _pub_collection_paths(paths)
    migrated, pending = _pub_series_layout(groups, pub_colls)
    for spath, sids in sorted(pending.items()):
        print(f"[catalog] {spath}: {len(sids)} publication(s) not yet in the collection layout, "
              f"series stays in the old layout: {', '.join(sids[:5])}{' ...' if len(sids) > 5 else ''}",
              file=sys.stderr)
    awaiting = sorted(s for s in migrated if s in groups)
    if awaiting:
        print(f"[catalog] {len(awaiting)} migrated publication series still hold old-layout items "
              f"awaiting prune --legacy: {', '.join(awaiting)}", file=sys.stderr)
    orphans = sorted(g for g in groups if _is_pub_collection_path(g) and g not in pub_colls)
    problems = [f"publication items with no collection.json: {g}" for g in orphans]
    for g in list(groups):
        if g in migrated or (_is_pub_collection_path(g)
                             and (g in orphans or g.rsplit("/", 1)[0] not in migrated)):
            del groups[g]
    problems += _duplicate_item_ids(groups)
    pub_colls = {p: 0 for p in pub_colls if p.rsplit("/", 1)[0] in migrated}
    pub_mappable: set[str] = set()
    # Each publication's Items as index rows only: the full docs (table:columns, proj:wkt2) are
    # dropped as each collection is read, so memory does not grow with every pub Item.
    pub_rows: dict[str, list[dict]] = {}

    # 1. Write each leaf collection.json + items.json (flat or nested). collection id = the
    #    path's last segment (a series code when nested); title from the items' pub type.
    #    Record per-path {id, title, count} so the hierarchy links can carry counts.
    leaf: dict[str, dict] = {}
    rollup: dict[str, list[dict]] = {}   # nesting catalog -> its children's items (see ROLLUP_INDEX_CATALOGS)
    everything: list[dict] = []          # every item's index entry, for the root items.json
    dropped: list[str] = []              # items that failed to load, reported as problems
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
                    msg = (f"dropped {path}/{iid} from the index: "
                           f"{type(e).__name__}: {(str(e).splitlines() or [''])[0]}")
                    print(f"[catalog] {msg}", file=sys.stderr)
                    dropped.append(msg)
                    return None

            items = [it for it in executor.map(_fetch_one, sorted(item_ids)) if it is not None]
            if has_feature_service(path):
                served.extend(items)
            nested = "/" in path
            top, cid = path.split("/")[0], path.split("/")[-1]
            if _is_pub_collection_path(path):
                # Publication collections are written by the pubs ingest; refresh only indexes them.
                # The count is of Items that loaded, the ones the indexes list.
                pub_colls[path] = len(items)
                pub_rows[path] = [_index_entry(it, prefix=f"{path}/") for it in items]
                if any(_is_mappable(it) for it in items):
                    pub_mappable.add(path)
                del items
                continue
            # Title/description are inherited, never authored here: see _group_title. A group with
            # nothing to inherit stays None and _collection_doc falls back to prettify(id).
            title = _group_title(top, items) if nested else None
            mappable = sum(1 for it in items if _is_mappable(it))
            # Series blurbs are keyed by short codes (M, C, B…) — only look them up under a pub
            # catalog, or a raster layer that happened to be named `M` would inherit the Map series text.
            desc = SERIES_DESC.get(cid) if top in PUB_GROUPS else None
            if nested and top in ROLLUP_INDEX_CATALOGS:
                rollup.setdefault(top, []).extend(items)
            # From the fetched docs, so an item that failed to fetch just carries no title.
            item_titles = {it["id"]: it["properties"]["title"] for it in items
                           if it.get("id") and it.get("properties", {}).get("title")}
            # The mirror is derived from these same items, so it is rebuilt whenever the
            # collection is — the two cannot drift.
            mirror = item_mirror.write(path, items)
            layers = has_layer_collections(path)
            _write_json(_collection_doc(cid, path, item_ids, _extent(items), title=title,
                                        mappable=mappable, description=desc,
                                        item_titles=item_titles,
                                        assets=_collection_assets(path, items, mirror),
                                        layers=layers),
                        f"{config.STAC_PREFIX}/{path}/collection.json")
            _write_json(_index_doc(cid, items, layer=layers), f"{config.STAC_PREFIX}/{path}/items.json")
            everything.extend(_index_entry(it, prefix=f"{path}/", layer=layers) for it in items)
            if layers:
                list(executor.map(lambda it, p=path: _write_layer_collection(it, p), items))
            _write_markdown(path, title=title or prettify(cid),
                            description=desc or f"UGS warehouse — {title or cid}.",
                            kind="collection", children=len(item_ids),
                            extent=_extent(items), items=items,
                            service=has_feature_service(path))
            leaf[path] = {"id": cid, "title": title or prettify(cid), "count": len(item_ids),
                          "mappable": mappable}

    # 2. Build the hierarchy. A top-level segment with nested children (and no direct items)
    #    becomes a sub-catalog (e.g. ugs-serving-topics → one collection per topic, a publication
    #    group → its series); everything else is a flat collection directly under root. Child
    #    links carry counts.
    problems += sorted(dropped)
    pub_series, skipped, pub_docs = _write_pub_series(pub_colls, pub_mappable)
    problems += skipped.values()
    # One root entry per publication, plus each of its Items tagged with the publication so map
    # layer lists and searches still reach them. The series items.json lists publications only.
    for path, meta in pub_docs.items():
        everything.append(meta.row)
        everything.extend(_tag_pub_entry(row, meta.id, meta.title) for row in pub_rows.get(path, []))
    tops: dict[str, list[str]] = {}
    for path in groups:
        if not _is_pub_collection_path(path):
            tops.setdefault(path.split("/")[0], []).append(path)
    for group in pub_series:
        tops.setdefault(group, [])
    root_children = []
    for top, paths in tops.items():
        if top not in groups:  # sub-catalog (nested, no direct items)
            kids = [leaf[p] for p in sorted(paths)] + pub_series.get(top, [])
            ptitle = prettify(top.replace("ugs-", ""))
            rolled = rollup.get(top)
            if rolled is not None:  # one index spanning every child collection
                _write_json(_index_doc(top, rolled, rollup=True, layer=top == SERVING_TOPICS_CATALOG),
                            f"{config.STAC_PREFIX}/{top}/items.json")
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
    _write_json(_pub_coverage(migrated, pending, pub_docs, pub_rows),
                f"{config.STAC_PREFIX}/{PUB_COVERAGE_FILE}")
    _write_json(_root_doc(root_children), f"{config.STAC_PREFIX}/catalog.json")
    # Every item in one index, so a client that wants the whole catalog makes one request.
    _write_json({"type": "ugs-items-index", "collection": None, "count": len(everything),
                 "items": sorted(everything, key=lambda e: e["links"][0]["href"])},
                f"{config.STAC_PREFIX}/items.json")
    _write_markdown("", title=config.CATALOG_TITLE, description=ROOT_DESCRIPTION,
                    kind="catalog", children=len(root_children))
    feature_service.write(served)

    # Publication collections are counted once, from pub_colls (Items that loaded), not again
    # from their listed groups.
    written_pubs = [p for p in pub_colls if p not in skipped]
    plain = [v for g, v in groups.items() if not _is_pub_collection_path(g)]
    n = sum(len(v) for v in plain) + sum(pub_colls[p] for p in written_pubs)
    print(f"[catalog] {config.public_url(config.STAC_PREFIX + '/catalog.json')} "
          f"({len(plain) + len(written_pubs)} collections, {n} items)")
    for problem in problems:
        print(f"[catalog] {problem}", file=sys.stderr)
    if strict and problems:
        raise RuntimeError(f"[catalog] {len(problems)} problem(s) after the refresh completed: "
                           f"{'; '.join(problems)}")
    return problems
