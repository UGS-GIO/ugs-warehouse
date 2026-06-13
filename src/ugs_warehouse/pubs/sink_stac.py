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
from . import identity, topic

UGSPUB = "https://ugspub.nr.utah.gov/publications/"
LANDING = "https://geology.utah.gov/publication-details/?pub="
UGS_NAMES = {"UGS", "UGMS", "UTAH GEOLOGICAL SURVEY", "UTAH GEOLOGICAL AND MINERAL SURVEY"}

MEDIA = {".pdf": "application/pdf", ".zip": "application/zip",
         ".xlsx": "application/vnd.ms-excel", ".xls": "application/vnd.ms-excel",
         ".csv": "text/csv", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
         ".png": "image/png", ".txt": "text/plain"}
COG_MIME = "image/tiff; application=geotiff; profile=cloud-optimized"
PARQUET_MIME = "application/vnd.apache.parquet"

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


def build_item(p: dict, attachments: list[dict], *,
               geom: dict | None = None, bbox: list[float] | None = None,
               fp_source: str | None = None,
               has_cog: bool = False, has_units: bool = False,
               has_thumb: bool = False) -> dict:
    """Build a pub STAC Item (collection-nested, via core.stac.build_item)."""
    sid = (p.get("series_id") or "").strip()
    yr = (p.get("pub_year") or "").strip()
    dt = f"{yr}-01-01T00:00:00Z" if yr.isdigit() else None

    assets: dict = {}
    main_pdf = href(p.get("pub_url"))
    if main_pdf:
        assets["publication"] = {"href": main_pdf, "type": media_type(main_pdf),
                                 "title": "Publication", "roles": ["data"]}
    for a in attachments:
        h = href(a.get("pub_url"))
        if not h:
            continue
        key = re.sub(r"[^a-z0-9]+", "_", (a.get("extra_data") or "file").strip().lower()).strip("_") or "file"
        assets.setdefault(key, {"href": h, "type": media_type(h),
                                "title": (a.get("extra_data") or "").strip(), "roles": ["data"]})
    if has_cog:
        assets["cog"] = {"href": config.public_url(identity.Pub(sid.upper()).cog_object),
                         "type": COG_MIME, "title": "Cloud-Optimized GeoTIFF",
                         "roles": ["data", "cloud-optimized"]}
    if has_thumb:
        assets["thumbnail"] = {"href": config.public_url(f"{identity.COG_PREFIX}/{sid.upper()}.thumb.png"),
                               "type": "image/png", "title": "Thumbnail", "roles": ["thumbnail"]}
    if has_units:
        assets["units"] = {
            "href": config.public_url(f"{identity.UNITS_PREFIX}/{sid.upper()}/{sid.upper()}.units.parquet"),
            "type": PARQUET_MIME, "title": "Geologic unit polygons (GeoParquet)", "roles": ["data"]}

    extra_links = [{"rel": "via", "href": f"{LANDING}{sid}", "type": "text/html",
                    "title": "UGS publication landing page"}]
    if has_ugs_doi(p.get("pub_publisher")):
        extra_links.append({"rel": "cite-as", "href": f"https://doi.org/10.34191/{sid}"})

    extensions = []
    if has_cog:
        cog_url = config.public_url(identity.Pub(sid.upper()).cog_object)
        extra_links.append(stac.cog_link(cog_url))
        extensions.append(stac.WEB_MAP_LINKS_EXT)

    return stac.build_item(
        item_id=sid, collection=identity.PUBLICATIONS_COLLECTION,
        geometry=geom, bbox=bbox, datetime_iso=dt,
        properties={
            "title": (p.get("pub_name") or "").strip() or stac.prettify(sid),
            "description": (p.get("full_citation") or "").strip(),
            "ugs:pub_type": pub_type_of(p),
            "ugs:series": (p.get("series") or "").strip(),
            "ugs:scale": (p.get("pub_scale") or "").strip(),
            "ugs:author": (p.get("pub_author") or "").strip(),
            "ugs:topic": topic.classify(p.get("pub_name"), p.get("keywords")),
            "ugs:footprint_source": fp_source,
            "keywords": (p.get("keywords") or "").strip(),
        },
        assets=assets,
        extra_links=extra_links,
        stac_extensions=extensions or None,
    )
