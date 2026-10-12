"""Publications as STAC: one Collection per publication, with its data as Items.

The Collection carries the documents (PDF, cover, original zips) as assets and the DOI as
`cite-as`; the plate COG, units GeoParquet, extracted layers/tables and 3D fence are its Items,
with footprint geometry where it's a map. Built with `core.stac` and `core.config` CDN hrefs.

`ugs:pub_type` is a field, not 35 sub-collections.
"""
from __future__ import annotations

import datetime
import math
import os
import re
import sys
import urllib.parse

from ..core import config, stac
from . import counties, identity, topic
from .scale import MOSAIC_TIER_LABEL
from .threed import LINE_NAME, MESH_NAME, POLY_NAME, threed_object
from .vectors import VECTORS_PREFIX

UGSPUB = identity.UGSPUB
LANDING = "https://geology.utah.gov/publication-details/?pub="
UGS_NAMES = {"UGS", "UGMS", "UTAH GEOLOGICAL SURVEY", "UTAH GEOLOGICAL AND MINERAL SURVEY"}

# A client picks a viewer, a downloader or nothing at all from the media type, so an unmapped
# extension falling back to `application/octet-stream` costs the reader the file. `.tif` here is
# the publisher's plain scan; a COG we produced carries COG_MIME, set on the asset directly.
# The earliest pub_year in the publications source: the start of an undated item's interval.
EARLIEST_RECORD = "1886-01-01T00:00:00Z"

MEDIA = {".pdf": "application/pdf", ".zip": "application/zip",
         ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
         ".xls": "application/vnd.ms-excel",
         ".csv": "text/csv", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
         ".png": "image/png", ".txt": "text/plain",
         ".tif": "image/tiff", ".tiff": "image/tiff",
         ".html": "text/html", ".htm": "text/html",
         ".kmz": "application/vnd.google-earth.kmz",
         ".kml": "application/vnd.google-earth.kml+xml"}
COG_MIME = config.COG_MIME
PARQUET_MIME = config.PARQUET_MIME
# Everything outside RFC 3986's unreserved set needs escaping in a path segment. See `item_id_for`.
_UNSAFE_IN_PATH = re.compile(r"[^A-Za-z0-9._~-]+")

# series_id alpha prefix -> canonical pub type (the free-text `series` field is dirty).
PREFIX_TYPE = {
    "MD": "Mining District Files", "OFR": "Open File Report", "OF": "Open File Report",
    "M": "Map", "RI": "Report of Investigation",
    "I": "Miscellaneous Investigations Series Map", "MF": "Miscellaneous Field Studies Map",
    "MP": "Miscellaneous Publication", "SS": "Special Study", "SNT": "Survey Notes",
    "C": "Circular", "B": "Bulletin", "PI": "Public Information Series", "MO": "Monograph",
    "CR": "Contract Report", "GQ": "Geologic Quadrangle Map", "UGA": "Utah Geological Association",
    "QR": "Quarterly Review", "WRB": "Water Resource Bulletin", "CI": "Coal Investigations Map",
    "OG": "Oil and Gas Field Studies", "UGS": "Utah Geological Society", "BYU": "BYU Geology",
    "HD": "Hand-Drawn Geologic Map", "WCD": "Wetlands Contract Deliverable", "UG": "Utah Geology",
    "DS": "Data Series", "ES": "Earth Science Series", "FSM": "Field Studies Map",
    "TP": "Technical Publication", "TEM": "Trace Elements Memorandum",
}


def pub_type_of(p: dict) -> str:
    m = re.match(r"([A-Za-z]+)", (p.get("series_id") or "").strip())
    if m:
        t = PREFIX_TYPE.get(m.group(1).upper())
        if t:
            return t
    return (p.get("series") or "").strip() or "Other"


def keywords_of(raw: str | None) -> list[str]:
    """The source's subject string as the list STAC requires.

    The upstream value is one blob per publication: subject strings on their own lines, each a
    `;`-separated heading like `Geology; Summit County; Maps`. Commas are NOT separators — they sit
    inside a heading (`Tooele, Utah`) — so splitting on one would cut a keyword in half.
    """
    return list(dict.fromkeys(k.strip() for k in re.split(r"[;\n\r]", raw or "") if k.strip()))


def series_code(sid: str) -> str:
    """Data-series code = alpha prefix of the series id (DS-8 → DS). The nesting key under
    ugs-publications. Numeric/prefixless ids bucket as OTHER."""
    m = re.match(r"([A-Za-z]+)", (sid or "").strip())
    return m.group(1).upper() if m else "OTHER"


def item_id_for(sid: str) -> str:
    """The published item id: the source's series id, made safe to sit in a URI path.

    The id is also a directory name and the last segment of every link that reaches the item, so a
    space in it (`Geologic map of Utah`, `557.92 UT1CO`) put a raw space in the item's own `self`
    link, in its collection's `item` link and in the ISO sidecar URL. A browser re-encodes those
    silently; a strict client rejects them, which is what a static catalog is read by. The exact
    source string stays on the item as `ugs:series_id`.
    """
    return _UNSAFE_IN_PATH.sub("-", (sid or "").strip())


def issue_volume(sid: str) -> int | None:
    """Survey Notes volume from the id: SNT-{volume}-{issue} → volume (SNT-58-2 → 58, SNT-22-1-2 → 22).
    Lets the viewer group issues under their volume. None for non-SNT or unparseable ids."""
    m = re.match(r"^SNT-(\d+)-", (sid or "").strip().upper())
    return int(m.group(1)) if m else None


def media_type(url: str) -> str:
    return MEDIA.get(os.path.splitext(url.split("?")[0])[1].lower(), "application/octet-stream")


# Hosts we run, which all serve https. An http URL on one of them is upgraded; a third-party host
# is left exactly as the source gave it, since assuming TLS somewhere we do not operate would turn
# a working link into a broken one.
UGS_HOSTS = ("ugspub.nr.utah.gov", "geology.utah.gov", "maps-assets.geology.utah.gov")


def href(u: str | None) -> str | None:
    """The source's file reference as a URL, or None when it does not name a file.

    `undefined` is a JavaScript accident that reached the publications database as text. It
    resolved to `…/publications/undefined`, an asset that 404s by construction. `http` is upgraded
    on our own hosts: they answer the https URL, and a browser cannot fetch a plaintext asset from
    a page served over https.
    """
    u = (u or "").strip()
    if not u or u.lower() == "undefined":
        return None
    if u.startswith("http://") and u[len("http://"):].split("/")[0] in UGS_HOSTS:
        return "https://" + u[len("http://"):]
    return u if u.startswith("http") else UGSPUB + u.lstrip("/")


def has_ugs_doi(publisher: str | None) -> bool:
    """UGS mints 10.34191 DOIs — for UGS/UGMS (incl. co-pubs) and blank publisher; not pure-USGS."""
    tokens = {t.strip().upper() for t in re.split(r"[,;/&]", publisher or "") if t.strip()}
    return (not tokens) or bool(tokens & UGS_NAMES)


# USGS authored the Utah geologic-quad maps (GQ etc.) UGS publishes — kept in the main catalog.
CATALOG_PUBLISHERS = UGS_NAMES | {"USGS"}


def collection_group(p: dict) -> str:
    """Top-level STAC collection for a pub. UGS hosts third-party material it didn't author:
    Mining District Files (scanned mine docs, ~4.2k) and foreign-published pubs. Route those to
    sibling collections so the UGS geologic catalog (UGS/UGMS-authored + USGS Utah maps) stays
    clean — nothing dropped, just grouped. Blank publisher = treated as UGS (matches has_ugs_doi)."""
    if series_code(p.get("series_id")) == "MD":
        return identity.MINING_DISTRICT_COLLECTION
    tokens = {t.strip().upper() for t in re.split(r"[,;/&]", p.get("pub_publisher") or "") if t.strip()}
    if (not tokens) or (tokens & CATALOG_PUBLISHERS):
        return identity.PUBLICATIONS_COLLECTION
    return identity.EXTERNAL_COLLECTION


def cog_asset_fields(ds) -> dict:
    """STAC 1.1 asset fields for an open COG, read from its header with rio-stac (no pixel reads).

    rio-stac emits projection v1.1 `proj:epsg` and `eo:bands`; both map to their STAC 1.1 forms
    here (`proj:code` and common `bands`). Its `raster:bands` reads pixel statistics, so data type
    and nodata come from the header directly.
    """
    from rio_stac.stac import get_eobands_info, get_projection_info

    proj = get_projection_info(ds)
    epsg = proj.pop("epsg")
    proj.pop("geometry")
    fields = {f"proj:{k}": v for k, v in proj.items()}
    if epsg:
        fields["proj:code"] = f"EPSG:{epsg}"
    bands = get_eobands_info(ds)
    if len(set(ds.dtypes)) == 1:
        fields["data_type"] = ds.dtypes[0]
    else:
        for band, dtype in zip(bands, ds.dtypes):
            band["data_type"] = dtype
    if ds.nodata is not None:
        fields["nodata"] = ds.nodata if math.isfinite(ds.nodata) else str(ds.nodata)
    fields["bands"] = bands
    return fields


def pub_collection_path(p: dict) -> str:
    """Layout path of a publication's Collection: `<group>/<SERIES>/<SID>`."""
    sid = (p.get("series_id") or "").strip()
    return f"{collection_group(p)}/{series_code(sid)}/{item_id_for(sid)}"


def plate_item_id(sid: str) -> str:
    return f"{item_id_for(sid)}_plate"


def layer_item_id(sid: str, label: str) -> str:
    return f"{item_id_for(sid)}_{label}"


def is_single_file(*, has_cog: bool, has_units: bool, has_3d: bool,
                   vector_layers: list[str] | None, companion_tables: list[dict] | None) -> bool:
    """True when a publication has exactly one data Item. Portolan keeps a single-file collection's
    data at collection level, so such a publication is a Collection with assets and no Items."""
    return (int(has_cog) + int(has_units) + int(has_3d)
            + len(vector_layers or []) + len(companion_tables or [])) == 1


def plate_object(p: dict, *, single_file: bool) -> str:
    """Object path of the document that holds a publication's plate: its Collection when the plate
    is its only data file, else the `_plate` Item."""
    coll_path = pub_collection_path(p)
    if single_file:
        return stac.collection_object_path(coll_path)
    return stac.item_object_path(coll_path, plate_item_id((p.get("series_id") or "").strip()))


def _source_asset(h: str, mirrored: set[str] | None, **rest) -> dict:
    """A source file served from our CDN when mirrored, with the publisher's URL as an alternate."""
    obj = identity.pub_file_object(h)
    if not (obj and mirrored and obj in mirrored):
        return {"href": h, **rest}
    return {"href": identity.pub_file_url(obj), **rest, "alternate:name": "Warehouse CDN",
            "alternate": {"publisher": {"href": h, "alternate:name": "UGS publications site",
                                        "title": "Publisher copy (ugspub.nr.utah.gov)"}}}


def _file_roles(mtype: str) -> list[str]:
    """Original zips and publisher TIFFs are upstream originals (Portolan `source` role); other
    attachments are data."""
    return ["source"] if mtype in ("image/tiff", "application/zip") else ["data"]


def build_publication(p: dict, attachments: list[dict], *,
                      geom: dict | None = None, bbox: list[float] | None = None,
                      fp_source: str | None = None,
                      has_cog: bool = False, cog_fields: dict | None = None,
                      has_units: bool = False,
                      has_thumb: bool = False, has_cover: bool = False,
                      has_3d: bool = False, classes_3d: list[dict] | None = None,
                      vector_layers: list[str] | None = None,
                      companion_tables: list[dict] | None = None,
                      layers: dict | None = None,
                      override: dict | None = None,
                      contents: list[dict] | None = None,
                      mirrored: set[str] | None = None,
                      edition: dict | None = None,
                      mosaic_tier: str | None = None) -> tuple[dict, list[dict]]:
    """A publication as one STAC Collection plus its Items.

    The Collection carries the documents (PDF, cover, original zips) and the publication's
    metadata; the plate COG, the units GeoParquet, each extracted layer and table, and the 3D
    fence are Items in it. A publication with none of those, or with exactly one, is a Collection
    with no Items; a lone data file becomes a collection-level asset."""
    sid = (p.get("series_id") or "").strip()
    pub_id = item_id_for(sid)
    coll_path = pub_collection_path(p)
    yr = (p.get("pub_year") or "").strip()
    dt = f"{yr}-01-01T00:00:00Z" if yr.isdigit() else None
    now = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT00:00:00Z")
    undated = {} if dt else {"start_datetime": EARLIEST_RECORD, "end_datetime": now,
                             "ugs:date_unknown": True}
    ed = edition or {}
    ov = override or {}
    quoted_sid = urllib.parse.quote(sid, safe="")
    has_doi = has_ugs_doi(p.get("pub_publisher"))
    doi = [{"rel": "cite-as", "href": f"https://doi.org/10.34191/{quoted_sid}"}] if has_doi else []
    citation = (p.get("full_citation") or "").strip()
    sci = ({"sci:doi": f"10.34191/{sid}", **({"sci:citation": citation} if citation else {})}
           if has_doi else {})
    layer_meta = layers or {}
    aoi = geom or stac.bbox_polygon(stac.UTAH_BBOX)
    aoi_bbox = bbox or stac.UTAH_BBOX

    def crs_of(meta: dict) -> dict:
        """The layer's own CRS from its manifest: the EPSG code when GDAL matched one with
        confidence, else its WKT2. Nothing when neither was recorded."""
        if meta.get("proj_code"):
            return {"proj:code": meta["proj_code"]}
        return {"proj:wkt2": meta["proj_wkt2"]} if meta.get("proj_wkt2") else {}

    def item(item_id: str, title: str, assets: dict, *, geometry: dict, item_bbox: list[float],
             extra: dict | None = None, links: list[dict] | None = None,
             extensions: list[str] | None = None) -> dict:
        props = {"title": title, "ugs:series_id": sid, **undated, **sci, **(extra or {})}
        exts = list(extensions or [])
        if sci:
            exts.append(stac.SCI_EXT)
        if any(k.startswith("proj:") for k in props) and stac.PROJ_EXT not in exts:
            exts.append(stac.PROJ_EXT)
        if "table:row_count" in props and stac.TABLE_EXT not in exts:
            exts.append(stac.TABLE_EXT)
        return stac.build_item(
            item_id=item_id, collection=pub_id, collection_path=coll_path,
            geometry=geometry, bbox=item_bbox, datetime_iso=dt, properties=props,
            assets=assets, extra_links=[*doi, *(links or [])], stac_extensions=exts or None)

    for label in [*(vector_layers or []), *(t["label"] for t in companion_tables or [])]:
        if label not in layer_meta:
            print(f"[pubs] {pub_id}: no manifest entry for layer {label!r}; "
                  "its item has no CRS or row count", file=sys.stderr)

    reserved = {plate_item_id(sid), f"{pub_id}_units", f"{pub_id}_fence"}

    def layer_id(label: str) -> str:
        """The layer's item id, moved off the ids the plate, units and fence items own."""
        lid = layer_item_id(sid, label)
        if lid not in reserved:
            return lid
        new = f"{pub_id}_layer_{label}"
        print(f"[pubs] {pub_id}: layer {label!r} renamed to {new!r} (collides with a reserved item)",
              file=sys.stderr)
        return new

    mosaic = ([{"rel": "related",
                "href": config.public_url(stac.item_object_path("ugs-geologic-maps", f"geologic-maps-{mosaic_tier}")),
                "type": "application/geo+json",
                "title": f"Utah geologic maps — {MOSAIC_TIER_LABEL.get(mosaic_tier, mosaic_tier)} seamless mosaic"}]
              if has_cog and mosaic_tier and not ed.get("deprecated") else [])
    items: list[dict] = []
    if has_cog:
        plate_assets = {"cog": {"href": config.public_url(identity.Pub(sid.upper()).cog_object),
                                "type": COG_MIME, "title": "Cloud-Optimized GeoTIFF",
                                "roles": ["data", "cloud-optimized"], **(cog_fields or {})}}
        if has_thumb:
            plate_assets["thumbnail"] = {"href": config.public_url(identity.Pub(sid.upper()).thumb_object),
                                         "type": config.WEBP_MIME, "title": "Thumbnail", "roles": ["thumbnail"]}
        items.append(item(plate_item_id(sid), "Map plate", plate_assets, geometry=aoi, item_bbox=aoi_bbox,
                          links=mosaic,
                          extensions=[stac.PROJ_EXT] if any(k.startswith("proj:") for k in plate_assets["cog"]) else None))
    if has_units:
        # The units GeoParquet is written in EPSG:4326.
        items.append(item(f"{pub_id}_units", "Geologic unit polygons", {"data": {
            "href": config.public_url(f"{identity.UNITS_PREFIX}/{sid.upper()}/{sid.upper()}.units.parquet"),
            "type": PARQUET_MIME, "title": "Geologic unit polygons (GeoParquet)", "roles": ["data"]}},
            geometry=aoi, item_bbox=aoi_bbox, extra={"proj:code": "EPSG:4326"}))
    for label in vector_layers or []:
        meta = layer_meta.get(label, {})
        lb = meta.get("bbox")
        rows = {"table:row_count": meta["rows"]} if meta.get("rows") is not None else {}
        items.append(item(layer_id(label), stac.prettify(label), {"data": {
            "href": config.public_url(f"{VECTORS_PREFIX}/{sid.upper()}/{label}.parquet"),
            "type": PARQUET_MIME, "title": stac.prettify(label), "roles": ["data"]}},
            geometry=stac.bbox_polygon(lb) if lb else aoi, item_bbox=lb or aoi_bbox,
            extra={**crs_of(meta), **rows}))
    for t in companion_tables or []:
        label, meta = t["label"], layer_meta.get(t["label"], {})
        asset = {"href": config.public_url(f"{VECTORS_PREFIX}/{sid.upper()}/{label}.parquet"),
                 "type": PARQUET_MIME, "title": stac.prettify(label), "roles": ["data"]}
        if t.get("columns"):
            asset["table:columns"] = t["columns"]
        items.append(item(layer_id(label), stac.prettify(label), {"data": asset},
                          geometry=aoi, item_bbox=aoi_bbox,
                          extra={"table:row_count": meta["rows"]} if meta.get("rows") is not None else None,
                          extensions=[stac.TABLE_EXT] if t.get("columns") else None))
    if has_3d:
        items.append(item(f"{pub_id}_fence", "3D fence diagram", {
            "fence_polygons": {"href": config.public_url(threed_object(sid, POLY_NAME)), "type": PARQUET_MIME,
                               "roles": ["data", "3d-vector"], "title": "3D fence polygons (GeoParquet)"},
            "fence_lines": {"href": config.public_url(threed_object(sid, LINE_NAME)), "type": PARQUET_MIME,
                            "roles": ["data", "3d-vector"], "title": "3D fence contacts & faults (GeoParquet)"},
            "fence_mesh": {"href": config.public_url(threed_object(sid, MESH_NAME)), "type": "model/gltf-binary",
                           "roles": ["data", "visual"], "title": "3D fence mesh (glTF)"}},
            geometry=aoi, item_bbox=aoi_bbox,
            extra={"classification:classes": classes_3d} if classes_3d else None,
            extensions=[stac.CLASSIFICATION_EXT] if classes_3d else None))

    seen: set[str] = set()
    dupes = sorted({it["id"] for it in items if it["id"] in seen or seen.add(it["id"])})
    if dupes:
        raise ValueError(f"{pub_id}: duplicate item ids {dupes}")

    single = is_single_file(has_cog=has_cog, has_units=has_units, has_3d=has_3d,
                            vector_layers=vector_layers, companion_tables=companion_tables)
    if single != (len(items) == 1):
        raise ValueError(f"{pub_id}: single-file rule and built items disagree ({len(items)} items)")
    item_bboxes = [it["bbox"] for it in items if it.get("bbox")]
    data_assets: dict = {}
    data_exts: list[str] = []
    mosaic_links: list[dict] = []
    if single:
        # The lone Item's data moves to the Collection; its item-level CRS, row count and class
        # fields are only valid on an asset there, so they ride each asset.
        only = items.pop()
        carried = {k: v for k, v in only["properties"].items()
                   if k.startswith(("proj:", "table:", "classification:"))}
        data_assets = {k: {**a, **{ck: cv for ck, cv in carried.items() if ck not in a}}
                       for k, a in only["assets"].items() if k != "thumbnail"}
        data_exts = [e for e in only.get("stac_extensions", []) if e != stac.SCI_EXT]
        mosaic_links = mosaic

    assets: dict = {}
    main_pdf = href(p.get("pub_url"))
    if main_pdf:
        mtype = media_type(main_pdf)
        assets["publication"] = _source_asset(main_pdf, mirrored, type=mtype, title="Publication",
                                              roles=_file_roles(mtype))
    for a in attachments:
        h = href(a.get("pub_url"))
        if not h:
            continue
        mtype = media_type(h)
        key = re.sub(r"[^a-z0-9]+", "_", (a.get("extra_data") or "file").strip().lower()).strip("_") or "file"
        assets.setdefault(key, _source_asset(h, mirrored, type=mtype,
                                             title=(a.get("extra_data") or "").strip(), roles=_file_roles(mtype)))
    for key, asset in data_assets.items():
        new = key
        n = 1
        while new in assets or new in ("thumbnail", "overview"):
            n += 1
            new = f"{key}_{n}"
        if new != key:
            print(f"[pubs] {pub_id}: data asset {key!r} renamed to {new!r} (collides with a document asset)",
                  file=sys.stderr)
        assets[new] = asset
    plate_preview = ({"href": config.public_url(identity.Pub(sid.upper()).thumb_object),
                      "type": config.WEBP_MIME, "title": "Thumbnail", "roles": ["thumbnail"]}
                     if has_cog and has_thumb else None)
    if has_cover:
        assets["thumbnail"] = {"href": config.public_url(identity.pub_cover_object(sid)),
                               "type": config.WEBP_MIME, "title": "Cover (PDF first page)", "roles": ["thumbnail"]}
        if single and plate_preview:
            # The cover owns `thumbnail`; the plate's preview stays reachable as the overview.
            assets["overview"] = {**plate_preview, "title": "Map preview", "roles": ["overview"]}
    elif plate_preview:
        # No cover: the plate's own thumbnail stands in so the Collection still has a preview.
        assets["thumbnail"] = plate_preview

    ext_bbox = bbox or ([min(b[0] for b in item_bboxes), min(b[1] for b in item_bboxes),
                         max(b[2] for b in item_bboxes), max(b[3] for b in item_bboxes)]
                        if item_bboxes else stac.UTAH_BBOX)
    extent = {"spatial": {"bbox": [ext_bbox]},
              "temporal": {"interval": [[dt, dt] if dt else [EARLIEST_RECORD, now]]}}
    links = [{"rel": "alternate", "href": f"{LANDING}{quoted_sid}", "type": "text/html",
              "title": "UGS publication landing page"}, *doi, *mosaic_links]
    for rel, key in (("predecessor-version", "predecessor_href"), ("successor-version", "successor_href"),
                     ("latest-version", "latest_href")):
        if ed.get(key):
            links.append({"rel": rel, "href": ed[key], "type": "application/json"})
    # The old-path read keeps descriptions published as a single item before the collection layout.
    group, code = collection_group(p), series_code(sid)
    desc = ov.get("description") or (p.get("full_citation") or "").strip() \
        or (stac.prior_collection_field(coll_path, "description") or "") \
        or (stac.prior_property(f"{group}/{code}", pub_id, "description") or "")
    title = ov.get("title") or (p.get("pub_name") or "").strip() or stac.prettify(sid)
    fields = {
        "ugs:series_id": sid,
        **({"ugs:date_unknown": True} if not dt else {}),
        "ugs:pub_type": pub_type_of(p),
        **({"ugs:series": s} if (s := (p.get("series") or "").strip()) else {}),
        **({"ugs:scale": sc} if (sc := (p.get("pub_scale") or "").strip()) else {}),
        **({"ugs:author": au} if (au := (p.get("pub_author") or "").strip()) else {}),
        **({"version": v} if (v := ed.get("version")) else {}),
        **({"deprecated": True} if ed.get("deprecated") else {}),
        "ugs:topic": topic.classify(p.get("pub_name"), p.get("keywords")),
        "ugs:topic_category": "geoscientificInformation",
        **({"ugs:footprint_source": fp_source} if fp_source else {}),
        **({"keywords": kw} if (kw := keywords_of(p.get("keywords"))) else {}),
        **({"ugs:county": cty} if (cty := counties.county_of_pub(p)) else {}),
        **({"ugs:volume": vol} if (vol := issue_volume(sid)) is not None else {}),
        **({"ugs:contents": contents} if contents else {}),
        **sci,
    }
    coll_exts = list(dict.fromkeys([
        *([stac.SCI_EXT] if sci else []),
        *([stac.ALTERNATE_ASSETS_EXT] if any("alternate" in a for a in assets.values()) else []),
        *data_exts]))
    coll = stac.build_collection(
        collection_id=pub_id, collection_path=coll_path, title=title,
        description=desc or title, extent=extent, assets=assets,
        item_titles={it["id"]: it["properties"]["title"] for it in items},
        extra_links=links, fields=fields, stac_extensions=coll_exts or None)
    return coll, items
