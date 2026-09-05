"""Pub STAC items — emitted through the shared core into the `ugs-publications` collection.

Each UGS publication becomes a STAC Item: download links (PDF/zip/tables) as assets, the
harvested COG / units GeoParquet / thumbnail as cloud-native assets where present, footprint
geometry where it's a map, a `via` landing link, and a `cite-as` DOI for UGS-published pubs.
Ported from ugs-geolmap-cog-poc/catalog/build_pubs_stac.py — adapted to `core.stac`
(collection-nested layout, one catalog) and `core.config` CDN hrefs.

`ugs:pub_type` is a property (one `ugs-publications` collection), not 35 sub-collections.
"""
from __future__ import annotations

import os
import re

from ..core import config, stac
from . import counties, identity, topic
from .threed import LINE_NAME, MESH_NAME, POLY_NAME, threed_object

UGSPUB = identity.UGSPUB
LANDING = "https://geology.utah.gov/publication-details/?pub="
UGS_NAMES = {"UGS", "UGMS", "UTAH GEOLOGICAL SURVEY", "UTAH GEOLOGICAL AND MINERAL SURVEY"}

MEDIA = {".pdf": "application/pdf", ".zip": "application/zip",
         ".xlsx": "application/vnd.ms-excel", ".xls": "application/vnd.ms-excel",
         ".csv": "text/csv", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
         ".png": "image/png", ".txt": "text/plain"}
COG_MIME = config.COG_MIME
PARQUET_MIME = config.PARQUET_MIME

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


def issue_volume(sid: str) -> int | None:
    """Survey Notes volume from the id: SNT-{volume}-{issue} → volume (SNT-58-2 → 58, SNT-22-1-2 → 22).
    Lets the viewer group issues under their volume. None for non-SNT or unparseable ids."""
    m = re.match(r"^SNT-(\d+)-", (sid or "").strip().upper())
    return int(m.group(1)) if m else None


def media_type(url: str) -> str:
    return MEDIA.get(os.path.splitext(url.split("?")[0])[1].lower(), "application/octet-stream")


def href(u: str | None) -> str | None:
    u = (u or "").strip()
    if not u:
        return None
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


def build_item(p: dict, attachments: list[dict], *,
               geom: dict | None = None, bbox: list[float] | None = None,
               fp_source: str | None = None,
               has_cog: bool = False, has_units: bool = False,
               has_thumb: bool = False, has_cover: bool = False,
               has_3d: bool = False, classes_3d: list[dict] | None = None,
               override: dict | None = None,
               contents: list[dict] | None = None,
               mirrored: set[str] | None = None) -> dict:
    """Build a pub STAC Item (collection-nested, via core.stac.build_item).

    `mirrored` is the set of object paths the warehouse holds copies of (see pubs/mirror.py).
    Source files in it are served from our CDN; the rest stay linked to the publisher's host.
    """
    sid = (p.get("series_id") or "").strip()
    yr = (p.get("pub_year") or "").strip()
    dt = f"{yr}-01-01T00:00:00Z" if yr.isdigit() else None

    # A mirrored file is served from OUR CDN, with the publisher's URL kept as an `alternate` —
    # same bytes, two locations. Provenance survives, and a client that wants the publisher's copy
    # (or hits our CDN cold) still has it. Unmirrored files are unchanged: a plain legacy href.
    def source_asset(h: str, **rest) -> dict:
        obj = identity.pub_file_object(h)
        if not (obj and mirrored and obj in mirrored):
            return {"href": h, **rest}
        return {"href": identity.pub_file_url(obj), **rest, "alternate:name": "Warehouse CDN",
                "alternate": {"publisher": {"href": h, "alternate:name": "UGS publications site",
                                            "title": "Publisher copy (ugspub.nr.utah.gov)"}}}

    assets: dict = {}
    main_pdf = href(p.get("pub_url"))
    if main_pdf:
        assets["publication"] = source_asset(main_pdf, type=media_type(main_pdf),
                                             title="Publication", roles=["data"])
    for a in attachments:
        h = href(a.get("pub_url"))
        if not h:
            continue
        key = re.sub(r"[^a-z0-9]+", "_", (a.get("extra_data") or "file").strip().lower()).strip("_") or "file"
        assets.setdefault(key, source_asset(h, type=media_type(h),
                                            title=(a.get("extra_data") or "").strip(), roles=["data"]))
    if has_cog:
        # The COG is warped to EPSG:3857 (harvest.py: gdalwarp -t_srs + rio-cogeo web_optimized),
        # which differs from the item-level proj:code (4326, the footprint/units CRS). The projection
        # ext allows per-asset overrides, so stamp the COG's real CRS on the asset itself — otherwise
        # a client reads the item-level 4326 and mis-places the raster.
        # harvest produces an RGBA uint8 WebP COG (gdalwarp -dstalpha → rio-cogeo). Bands are the
        # STAC 1.1 common `bands` construct (NOT deprecated raster:bands); data_type is deduped to
        # the asset per 1.1 best practice. Alpha carries transparency, so no separate nodata.
        assets["cog"] = {"href": config.public_url(identity.Pub(sid.upper()).cog_object),
                         "type": COG_MIME, "title": "Cloud-Optimized GeoTIFF",
                         "roles": ["data", "cloud-optimized"], "proj:code": "EPSG:3857",
                         "data_type": "uint8",
                         "bands": [{"name": "red"}, {"name": "green"},
                                   {"name": "blue"}, {"name": "alpha"}]}
    if has_thumb:
        assets["thumbnail"] = {"href": config.public_url(f"{identity.COG_PREFIX}/{sid.upper()}.thumb.png"),
                               "type": "image/png", "title": "Thumbnail", "roles": ["thumbnail"]}
    # PDF first-page cover — a preview for ANY pub (incl. non-spatial). The harvested COG thumb (above)
    # is preferred when present (added first → the viewer picks it); this covers everything else.
    if has_cover:
        assets["preview"] = {"href": config.public_url(f"{identity.PUB_THUMB_PREFIX}/{sid.upper()}.png"),
                             "type": "image/png", "title": "Cover (PDF first page)", "roles": ["thumbnail"]}
    if has_units:
        assets["units"] = {
            "href": config.public_url(f"{identity.UNITS_PREFIX}/{sid.upper()}/{sid.upper()}.units.parquet"),
            "type": PARQUET_MIME, "title": "Geologic unit polygons (GeoParquet)", "roles": ["data"]}
    # Cloud-native 3D fence diagram (GeoParquet-3D polys/lines + glTF mesh), converted by pubs/threed
    # from the pub's CSA_3D gdb + .mapx. Presence-driven like the COG: the convert step writes the
    # artifacts, this stamps the assets. classification:classes (authored per-unit colors) rides in
    # the item properties — see the classification ext added below.
    if has_3d:
        assets["fence_polygons"] = {"href": config.public_url(threed_object(sid, POLY_NAME)),
                                    "type": PARQUET_MIME, "roles": ["data", "3d-vector"],
                                    "title": "3D fence polygons (GeoParquet)"}
        assets["fence_lines"] = {"href": config.public_url(threed_object(sid, LINE_NAME)),
                                 "type": PARQUET_MIME, "roles": ["data", "3d-vector"],
                                 "title": "3D fence contacts & faults (GeoParquet)"}
        assets["fence_mesh"] = {"href": config.public_url(threed_object(sid, MESH_NAME)),
                                "type": "model/gltf-binary", "roles": ["data", "visual"],
                                "title": "3D fence mesh (glTF)"}

    extra_links = [{"rel": "via", "href": f"{LANDING}{sid}", "type": "text/html",
                    "title": "UGS publication landing page"}]
    if has_ugs_doi(p.get("pub_publisher")):
        extra_links.append({"rel": "cite-as", "href": f"https://doi.org/10.34191/{sid}"})

    # No web-map-links here: that extension's rels are [xyz, wms, wmts, tilejson, pmtiles, 3d-tiles]
    # — it has no `cog`, and declaring it forces one of those (which a raster pub lacks). The COG is
    # advertised by its `cog` ASSET (media type `…;profile=cloud-optimized`), which STAC Browser and
    # our viewer both render natively, and which `_is_mappable`/`cogAsset` detect. No link needed.
    extensions: list[str] = []
    if has_cog:
        extensions.append(stac.PROJ_EXT)  # asset-level proj:code on the COG (EPSG:3857)
    if any("alternate" in a for a in assets.values()):
        extensions.append(stac.ALTERNATE_ASSETS_EXT)  # mirrored file + publisher copy
    if has_3d and classes_3d:
        extensions.append(stac.CLASSIFICATION_EXT)  # per-unit authored colors for the 3D fence

    code = series_code(sid)
    group = collection_group(p)  # top-level: UGS catalog / mining-district files / external
    # Precedence: hand-authored override (ops console) > source metadata > prior published value
    # (preserve-on-empty). So an operator's description/title wins + survives reingest, and a resubmit
    # with no citation keeps the published description instead of blanking it.
    ov = override or {}
    desc = ov.get("description") or (p.get("full_citation") or "").strip() \
        or (stac.prior_property(f"{group}/{code}", sid, "description") or "")
    title = ov.get("title") or (p.get("pub_name") or "").strip() or stac.prettify(sid)
    return stac.build_item(
        item_id=sid, collection=code,
        collection_path=f"{group}/{code}",
        geometry=geom, bbox=bbox, datetime_iso=dt,
        properties={
            "ugs:series_id": sid,  # the publication series id (== item id), surfaced as a labeled prop
            "title": title,
            # STAC gives `description` a minimum length, so a pub with no citation omits the field
            # rather than publishing "". Same for the UGS-prefixed strings below: an empty value
            # says nothing that an absent key does not.
            **({"description": desc} if desc else {}),
            "ugs:pub_type": pub_type_of(p),
            **({"ugs:series": s} if (s := (p.get("series") or "").strip()) else {}),
            **({"ugs:scale": sc} if (sc := (p.get("pub_scale") or "").strip()) else {}),
            **({"ugs:author": au} if (au := (p.get("pub_author") or "").strip()) else {}),
            "ugs:topic": topic.classify(p.get("pub_name"), p.get("keywords")),
            # ISO topic category. AUTHORED, not defaulted: a UGS publication is our own product, so
            # asserting the category is a statement about our own work — unlike a serving topic,
            # where an uncurated value would be a guess about someone else's data and is omitted
            # instead (#53). Pubs have no schema_registry row, so this is the only place to say it.
            "ugs:topic_category": "geoscientificInformation",
            "ugs:footprint_source": fp_source,
            **({"keywords": kw} if (kw := keywords_of(p.get("keywords"))) else {}),
            # County derived from the pub's lat/lon via the vendored SGID boundaries (point-in-polygon).
            # Only present for pubs that carry coordinates; empty values are dropped by the index.
            **({"ugs:county": cty} if (cty := counties.county_of_pub(p)) else {}),
            # Survey Notes volume (from SNT-{vol}-{issue}) so the viewer can group issues by volume.
            **({"ugs:volume": vol} if (vol := issue_volume(sid)) is not None else {}),
            # Survey Notes "In this issue": [{title, page}] parsed from the PDF TOC (or hand-authored).
            # UGS-prefixed custom field — no STAC extension fits; the viewer renders an issue contents list.
            **({"ugs:contents": contents} if contents else {}),
            # Authored per-unit colors for the 3D fence (classification ext), persisted by the convert
            # step and read back here so a pubs-ingest rebuild keeps them.
            **({"classification:classes": classes_3d} if has_3d and classes_3d else {}),
        },
        assets=assets,
        extra_links=extra_links,
        stac_extensions=extensions or None,
        proj_epsg=4326 if bbox else None,  # footprints/units are 4326
    )
