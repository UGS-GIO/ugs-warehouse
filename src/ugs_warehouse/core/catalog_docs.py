"""`README.md` and `AGENTS.md` beside every catalog and collection document.

Portolan requires both on every node (`PTL-FIL-001`), linked from the STAC JSON with `rel:agents`
and `rel:describedby` (`PTL-FIL-002/003`), and the README has to carry a title, a description, the
license and the provenance (`PTL-FIL-004/005`).

They are GENERATED, not authored. `refresh_catalog()` rebuilds the JSON from the objects in the
bucket on every run, and a hand-written file beside a document that regenerates would drift from it
the first time an item changed. Everything here is derived from values the catalog already holds —
title, description, license, providers, extent, what the items actually carry — so the prose cannot
say something the metadata contradicts.

The two files answer different questions. The README tells a person what this is and whether they
may use it. AGENTS.md tells a program how to read it without downloading everything first.
"""
from __future__ import annotations

from . import config
from .bbox import to_2d_bbox

README_NAME = "README.md"
AGENTS_NAME = "AGENTS.md"
MARKDOWN_MIME = "text/markdown"

# What a reader does with each format, keyed by the media type our assets carry. An asset type we
# do not recognise simply goes unmentioned rather than getting invented advice.
_HOWTO = {
    config.PARQUET_MIME: ("GeoParquet", "read it with DuckDB (`SELECT * FROM read_parquet('<href>')`), "
                                        "GeoPandas, or any Arrow reader; it is range-readable, so a "
                                        "spatial filter does not download the file"),
    config.PMTILES_MIME: ("PMTiles", "point a MapLibre or Leaflet PMTiles source at the href; it is a "
                                     "single file read by range request, with no tile server"),
    config.COG_MIME: ("Cloud-Optimized GeoTIFF", "open it with GDAL or rasterio over `/vsicurl/`, or "
                                                 "any COG-aware web client; overviews and internal "
                                                 "tiling make a windowed read cheap"),
}


def markdown_links() -> list[dict]:
    """The two links every catalog and collection must carry to its sibling Markdown."""
    return [
        {"rel": "describedby", "href": f"./{README_NAME}", "type": MARKDOWN_MIME, "title": "README"},
        {"rel": "agents", "href": f"./{AGENTS_NAME}", "type": MARKDOWN_MIME, "title": "AGENTS.md"},
    ]


def _providers_line() -> str:
    names = ", ".join(p["name"] for p in config.PROVIDERS)
    return names or "Utah Geological Survey"


def _formats(items: list[dict]) -> list[tuple[str, str]]:
    """The (format, how to read it) pairs the collection's assets actually justify."""
    seen: dict[str, tuple[str, str]] = {}
    for item in items:
        for asset in (item.get("assets") or {}).values():
            howto = _HOWTO.get(asset.get("type") or "")
            if howto:
                seen[howto[0]] = howto
    return sorted(seen.values())


def _extent_line(extent: dict | None) -> str:
    bbox = ((extent or {}).get("spatial") or {}).get("bbox") or []
    if not bbox or len(bbox[0]) not in (4, 6):
        return ""
    w, s, e, n = to_2d_bbox(bbox[0])
    return f"- Extent (WGS84): {w:.3f}, {s:.3f} to {e:.3f}, {n:.3f}\n"


def readme(*, title: str, description: str, kind: str, children: int,
           extent: dict | None = None, items: list[dict] | None = None,
           license_id: str | None = None) -> str:
    """The human entry point. `kind` is "catalog" or "collection"; `children` counts what it holds."""
    noun = "collections" if kind == "catalog" else "items"
    lic = license_id or config.DATA_LICENSE
    out = [f"# {title}\n\n", f"{description}\n\n"]
    out.append(f"- {children} {noun}\n")
    out.append(_extent_line(extent))
    # PTL-FIL-005 wants the license and the provenance in the README itself, not only in the JSON.
    out.append(f"- License: {lic} ({config.LICENSE_URL})\n")
    out.append(f"- Produced and published by: {_providers_line()}\n")
    formats = _formats(items or [])
    if formats:
        out.append("\n## Reading the data\n\n")
        out.extend(f"- **{name}** — {how}\n" for name, how in formats)
    out.append(f"\nThe machine-readable description of this {kind} is `{'catalog' if kind == 'catalog' else 'collection'}.json` "
               f"beside this file. `{AGENTS_NAME}` covers access patterns for programs.\n")
    return "".join(out)


def agents(*, title: str, kind: str, path: str, children: int,
           items: list[dict] | None = None, service: bool = False) -> str:
    """What a program needs to use this node without fetching everything first.

    `service` is True for the nodes the Features service serves live (flat collections and the
    serving-topic schemas, per `core.stac.has_feature_service`). Those DO have a query endpoint —
    OGC API Features — so the note names it and steers bulk/whole-layer work to the GeoParquet asset
    instead, rather than the old blanket claim that no query endpoint exists (#280).
    """
    base = config.public_url(f"{config.STAC_PREFIX}/{path}").rstrip("/") if path else \
        config.public_url(config.STAC_PREFIX)
    doc = "catalog.json" if kind == "catalog" else "collection.json"
    if service:
        api_note = ("The catalog itself is static JSON, read by following links; a live OGC API "
                    "Features service also answers queries (see Querying).")
    elif kind == "catalog":
        # A catalog spans collections that may or may not be served, so it must not deny a query
        # endpoint globally — a serving-topic collection under the root has one, and the root is the
        # natural entry point (#280 one level up). Point at the per-collection note instead.
        api_note = ("The catalog is static JSON, read by following links. Where a dataset offers a "
                    "live query service (OGC API Features), its own collection says so.")
    else:
        # A static leaf collection (e.g. a publication series) — its data really has no query API.
        api_note = ("Every object is static JSON; this collection has no query endpoint — read its "
                    "assets directly.")
    out = [f"# {title} — notes for agents\n\n",
           f"STAC {kind} at `{base}/{doc}`, served from the maps-assets CDN. {api_note}\n\n",
           "## Getting the contents\n\n"]
    if kind == "catalog":
        out.append(f"Follow the `rel:child` links in `{doc}`. Each carries a title and a "
                   "`ugs:item_count`, so a listing needs one fetch rather than one per child.\n")
        if not path:
            out.append("\nTo get every item at once, read `items.json` beside this file: one index "
                       "of the whole catalog, each entry with a `self` link to its full item.\n")
    else:
        out.append(f"Follow the `rel:item` links in `{doc}`; each carries the item's title, so a "
                   "listing does not need to fetch every item.\n")
        out.append("An `items.parquet` asset with the `collection-mirror` role, where present, "
                   "holds the same items in one file — prefer it for anything spatial or bulk.\n")
    formats = _formats(items or [])
    if formats:
        out.append("\n## Assets you will find\n\n")
        out.extend(f"- **{name}** — {how}\n" for name, how in formats)
    if service:
        # The endpoint that motivated #280: a consumer that loads the whole OGC API Features
        # collection to draw it OOMs the service. Name the query endpoint AND say what each thing is
        # for — filtered queries on the API, whole-layer/bulk on the GeoParquet asset.
        out.append("\n## Querying\n\n"
                   "These items are also served live via OGC API Features — each item carries a "
                   "`rel:service` link to its own `/collections/<item id>` endpoint. Use it for "
                   "attribute queries and server-side spatial filters that return a few features. "
                   "For a whole layer, or bulk or spatial analysis, read the GeoParquet `data` asset "
                   "directly (it is range-readable) instead of paging the API — that is the cheaper "
                   "path and avoids overloading the service.\n")
    out.append("\n## Conventions\n\n"
               "- Geometry and `bbox` are WGS84 (EPSG:4326). An asset in another projection carries "
               "its own `proj:code`, which overrides the item's.\n"
               "- Fields prefixed `ugs:` are ours and are not part of any STAC extension. Standard "
               "clients ignore them safely.\n"
               "- Assets the warehouse hosts carry `file:size` and `file:checksum`; one without a "
               "checksum has not been hashed yet. Files linked on other hosts carry neither.\n")
    return "".join(out)
