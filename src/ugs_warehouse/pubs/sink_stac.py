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
from collections import Counter

from ..core import config, gcs, stac
from ..core.bbox import to_2d_bbox
from ..core.stac import item_id_for, plate_item_id  # noqa: F401 (re-exported)
from . import counties, dois, identify, identity, topic
from .relations import layer_kind, source_of_label
from .scale import MOSAIC_TIER_LABEL
from .threed import LINE_NAME, MESH_NAME, POLY_NAME, threed_object
from .vectors import GEOPACKAGE_MIME, VECTORS_PREFIX, geopackage_object, pmtiles_object

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
# `ugs:footprint_source` values beyond the footprints parquet's own ("cog", "view"): the Utah
# bounding box standing in for a missing footprint, and a layer's bbox from its manifest.
FOOTPRINT_FALLBACK = stac.PLACEHOLDER_FOOTPRINT
LAYER_BBOX_SOURCE = "layer"
# A table with no geometry of its own sits on its publication's footprint.
TABLE_FOOTPRINT_SOURCE = "publication"
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


# USGS authored the Utah geologic-quad maps (GQ etc.) UGS publishes — kept in the main catalog.
CATALOG_PUBLISHERS = UGS_NAMES | {"USGS"}


def collection_group(p: dict) -> str:
    """Top-level STAC collection for a pub. UGS hosts third-party material it didn't author:
    Mining District Files (scanned mine docs, ~4.2k) and foreign-published pubs. Route those to
    sibling collections so the UGS geologic catalog (UGS/UGMS-authored + USGS Utah maps) stays
    clean — nothing dropped, just grouped. Blank publisher = treated as UGS."""
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


def layer_item_id(sid: str, label: str) -> str:
    """`<pub id>__<label>`. The double underscore keeps a layer's id off another publication's id
    (pub ids can hold `_`) and off the plate, units and fence Items (`<pub id>_plate`, ...)."""
    return f"{item_id_for(sid)}__{label}"


def layer_title(label: str) -> tuple[str, str | None]:
    """`(title, description)` for an extracted layer or table label. A `<dir>__` prefix (the
    extractor's uniqueness prefix) moves to the description; CamelCase and underscores split into
    words; all-caps and mixed-case tokens and digits stay as written, and all-lower words are
    capitalised."""
    prefix, sep, rest = label.partition("__")
    if not (sep and prefix and rest):
        prefix, rest = "", label
    words = [w for part in rest.split("_") for w in re.split(r"(?<=[a-z])(?=[A-Z])", part) if w]
    title = " ".join(w.capitalize() if w.islower() else w for w in words) or label
    return title, f"From {prefix}" if prefix else None


# Folder names that say what kind of file is inside, not which dataset it is.
_GENERIC_FOLDERS = {"shapefiles", "shapefile", "shp", "gis", "gis_data", "gisdata", "data",
                    "database", "gdb", "geodatabase"}


def source_title(source: str) -> str:
    """A source dataset's title from its whole folder path (`Cedar_Mesa_S__Shapefiles` -> "Cedar
    Mesa S"): each folder read as words, generic folder names and a `.gdb` extension dropped, the
    rest joined with " · ". With nothing distinctive left, the last folder's name."""
    parts = [p for p in source.split("__") if p]
    kept = [p for p in (re.sub(r"\.gdb$", "", p, flags=re.IGNORECASE) for p in parts)
            if p and p.lower() not in _GENERIC_FOLDERS]
    return " · ".join(layer_title(p)[0] for p in kept or parts[-1:] or [source])


def layer_titles(labels: list[str]) -> dict[str, tuple[str, str | None]]:
    """`layer_title` for every layer and table label of one publication. Where two labels read as
    the same title, each that has a `<dir>__` prefix carries the dir in its title, e.g.
    "Contacts And Faults (gems)", so the publication's list tells them apart."""
    base = {label: layer_title(label) for label in labels}
    seen = Counter(title.casefold() for title, _ in base.values())
    out = {}
    for label, (title, desc) in base.items():
        prefix, sep, rest = label.partition("__")
        if seen[title.casefold()] > 1 and sep and prefix and rest:
            title = f"{title} ({prefix})"
        out[label] = (title, desc)
    return out


# GeMS validation output (`T_1_LineErrors`, `T_2_PointErrors`): the validation tool's report on
# the map, not map data, so it is never published.
_GEMS_ERRORS = re.compile(r"^T_\d+_.*Errors$", re.IGNORECASE)


def is_validation_layer(label: str) -> bool:
    """True for a GeMS validation-error layer, judged on the label after any `<dir>__` prefix."""
    prefix, sep, rest = label.partition("__")
    return bool(_GEMS_ERRORS.match(rest if sep and prefix and rest else label))


def is_single_file(*, has_cog: bool, has_units: bool, has_3d: bool,
                   vector_layers: list[str] | None,
                   companion_tables: list[dict] | list[str] | None) -> bool:
    """True when a publication has exactly one data Item. Portolan keeps a single-file collection's
    data at collection level, so such a publication is a Collection with assets and no Items.
    Validation-error layers are not published, so they do not count. A companion table is a
    `{"label": ...}` dict (build_publication) or the bare label (a manifest's `tables` list)."""
    layers = [lb for lb in vector_layers or [] if not is_validation_layer(lb)]
    tables = [t for t in companion_tables or []
              if not is_validation_layer(t if isinstance(t, str) else t["label"])]
    return (int(has_cog) + int(has_units) + int(has_3d) + len(layers) + len(tables)) == 1


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
                      relationships: list[dict] | None = None,
                      empty_layers: list[str] | None = None,
                      override: dict | None = None,
                      contents: list[dict] | None = None,
                      mirrored: set[str] | None = None,
                      edition: dict | None = None,
                      mosaic_tier: str | None = None,
                      geopackage: dict | None = None,
                      unit_polys: dict | None = None,
                      registered_dois: frozenset[str] = frozenset()) -> tuple[dict, list[dict]]:
    """A publication as one STAC Collection plus its Items.

    The Collection carries the documents (PDF, cover, original zips) and the publication's
    metadata; the plate COG, the units GeoParquet, each extracted layer and table, and the 3D
    fence are Items in it. A publication with none of those, or with exactly one, is a Collection
    with no Items; a lone data file becomes a collection-level asset. `relationships` (the vector
    manifest's) become related-table assets, `ugs:foreign_keys` and `related` links on the layer
    and table Items, in the serving topics' shape. `empty_layers` are the manifest's layers with no
    rows, which were never uploaded. `geopackage` (the manifest's {bytes, checksum}) adds the
    all-layers GeoPackage as a Collection asset. `unit_polys` (the manifest's record of the normalized
    unit polygons) adds the plate Item's `units` asset, the identify tiles for clicks on the plate.
    `registered_dois` (`dois.registered`) decides the DOI: `sci:doi` and the `cite-as` link are
    written only for a publication whose DOI is in it."""
    sid = (p.get("series_id") or "").strip()
    pub_id = item_id_for(sid)
    coll_path = pub_collection_path(p)
    for label in [*(vector_layers or []), *(t["label"] for t in companion_tables or [])]:
        if is_validation_layer(label):
            print(f"[pubs] {pub_id}: skipped {label!r} (GeMS validation output, not map data)",
                  file=sys.stderr)
    unpublished = {lb for lb in [*(vector_layers or []), *(t["label"] for t in companion_tables or [])]
                   if is_validation_layer(lb)} | set(empty_layers or [])
    vector_layers = [lb for lb in vector_layers or [] if not is_validation_layer(lb)]
    companion_tables = [t for t in companion_tables or [] if not is_validation_layer(t["label"])]
    yr = (p.get("pub_year") or "").strip()
    dt = f"{yr}-01-01T00:00:00Z" if yr.isdigit() else None
    now = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT00:00:00Z")
    undated = {} if dt else {"start_datetime": EARLIEST_RECORD, "end_datetime": now,
                             "ugs:date_unknown": True}
    ed = edition or {}
    ov = override or {}
    quoted_sid = urllib.parse.quote(sid, safe="")
    # The scientific extension's DOI pattern forbids whitespace, so a spaced id carries no DOI.
    has_doi = dois.doi_of(sid) in registered_dois and not re.search(r"\s", sid)
    doi = [{"rel": "cite-as", "href": f"https://doi.org/10.34191/{quoted_sid}"}] if has_doi else []
    citation = (p.get("full_citation") or "").strip()
    sci = ({"sci:doi": f"10.34191/{sid}", **({"sci:citation": citation} if citation else {})}
           if has_doi else {})
    layer_meta = layers or {}
    aoi = geom or stac.bbox_polygon(stac.UTAH_BBOX)
    aoi_bbox = bbox or stac.UTAH_BBOX
    aoi_source = fp_source if geom else FOOTPRINT_FALLBACK

    def crs_of(meta: dict) -> dict:
        """The layer's own CRS from its manifest: the EPSG code when GDAL matched one with
        confidence, else its WKT2. Nothing when neither was recorded."""
        if meta.get("proj_code"):
            return {"proj:code": meta["proj_code"]}
        return {"proj:wkt2": meta["proj_wkt2"]} if meta.get("proj_wkt2") else {}

    def item(item_id: str, title: str, assets: dict, *, geometry: dict | None,
             item_bbox: list[float] | None, footprint_source: str | None = None,
             extra: dict | None = None, links: list[dict] | None = None,
             extensions: list[str] | None = None) -> dict:
        props = {"title": title, "ugs:series_id": sid, **undated, **sci, **(extra or {}),
                 **({"ugs:footprint_source": footprint_source} if footprint_source else {})}
        exts = list(extensions or [])
        if sci:
            exts.append(stac.SCI_EXT)
        return stac.build_item(
            item_id=item_id, collection=pub_id, collection_path=coll_path,
            geometry=geometry, bbox=item_bbox, datetime_iso=dt, properties=props,
            assets=assets, extra_links=[*doi, *(links or [])], stac_extensions=exts or None)

    for label in [*(vector_layers or []), *(t["label"] for t in companion_tables or [])]:
        if label not in layer_meta:
            print(f"[pubs] {pub_id}: no manifest entry for layer {label!r}; "
                  "its item has no CRS or row count", file=sys.stderr)

    taken: dict[str, str] = {}

    def layer_id(label: str) -> str:
        """The layer's item id, made unique ignoring case (item directories merge on
        case-insensitive filesystems)."""
        lid = layer_item_id(sid, label)
        new, n = lid, 1
        while taken.get(new.casefold(), new) != new:
            n += 1
            new = f"{lid}_{n}"
        if new != lid:
            print(f"[pubs] {pub_id}: layer {label!r} renamed to {new!r} (collides ignoring case)",
                  file=sys.stderr)
        taken[new.casefold()] = new
        return new

    labels = [*(vector_layers or []), *(t["label"] for t in companion_tables or [])]
    ids = {label: layer_id(label) for label in labels}
    titles = layer_titles(labels)
    rel_assets, rel_links, own_fks, published = _relationship_parts(
        sid, coll_path, relationships or [], ids, titles,
        {t["label"]: t.get("columns") for t in companion_tables or []}, set(vector_layers or []),
        unpublished, {k: m["rows"] for k, m in layer_meta.items() if m.get("rows") is not None})

    def kind_props(label: str, spatial: bool) -> dict:
        """The Item's `ugs:layer_kind` (from the relationships published here), its source dataset's
        key and its humanised title, and the title of the attachment it was extracted from."""
        source = layer_meta.get(label, {}).get("source") or source_of_label(label, sid)
        file = layer_meta.get(label, {}).get("source_file")
        return {"ugs:layer_kind": layer_kind(spatial, label, published),
                "ugs:source_key": source, "ugs:source_dataset": source_title(source),
                **({"ugs:source_file": file["title"]} if file else {}),
                **({"ugs:geometry_kind": gk} if spatial and (gk := geometry_kind(
                    layer_meta.get(label, {}).get("geometry_types"))) else {})}

    def with_related(label: str, data: dict) -> tuple[dict, list[str]]:
        """The Item's assets: its data (carrying its own foreign keys), then its related tables
        keyed by label, moved off a key the data or tiles use."""
        if own_fks.get(label):
            data["ugs:foreign_keys"] = own_fks[label]
        assets = {"data": data}
        for key, asset in rel_assets.get(label, {}).items():
            new, n = key, 1
            while new in assets or new in ("pmtiles", "thumbnail"):
                n += 1
                new = f"{key}_{n}"
            assets[new] = asset
        exts = [stac.TABLE_EXT] if any("table:columns" in a for a in assets.values()) else []
        return assets, exts

    mosaic = ([stac.related_link(
        "ugs-geologic-maps", f"geologic-maps-{mosaic_tier}",
        f"Utah geologic maps — {MOSAIC_TIER_LABEL.get(mosaic_tier, mosaic_tier)} seamless mosaic")]
        if has_cog and mosaic_tier and not ed.get("deprecated") else [])
    items: list[dict] = []
    if has_cog:
        plate_assets = {"cog": {"href": config.public_url(identity.Pub(sid.upper()).cog_object),
                                "type": COG_MIME, "title": "Cloud-Optimized GeoTIFF",
                                "roles": ["data", "cloud-optimized"], **(cog_fields or {})}}
        units_asset = identify.plate_asset(sid, unit_polys)
        if units_asset:
            plate_assets["units"] = units_asset
        if has_thumb:
            plate_assets["thumbnail"] = {"href": config.public_url(identity.Pub(sid.upper()).thumb_object),
                                         "type": config.WEBP_MIME, "title": "Thumbnail", "roles": ["thumbnail"]}
        items.append(item(plate_item_id(sid), "Map plate", plate_assets, geometry=aoi, item_bbox=aoi_bbox,
                          footprint_source=aoi_source, links=mosaic,
                          extensions=[stac.PROJ_EXT] if any(k.startswith("proj:") for k in plate_assets["cog"]) else None))
    if has_units:
        # The units GeoParquet is written in EPSG:4326.
        items.append(item(f"{pub_id}_units", "Geologic unit polygons", {"data": {
            "href": config.public_url(f"{identity.UNITS_PREFIX}/{sid.upper()}/{sid.upper()}.units.parquet"),
            "type": PARQUET_MIME, "title": "Geologic unit polygons (GeoParquet)", "roles": ["data"]}},
            geometry=aoi, item_bbox=aoi_bbox, footprint_source=aoi_source, extra={"proj:code": "EPSG:4326"}))
    for label in vector_layers or []:
        meta = layer_meta.get(label, {})
        generated = None
        lb = meta.get("bbox")
        rows = {"table:row_count": meta["rows"]} if meta.get("rows") is not None else {}
        ltitle, ldesc = titles[label]
        data = {"href": config.public_url(f"{VECTORS_PREFIX}/{sid.upper()}/{label}.parquet"),
                "type": PARQUET_MIME, "title": ltitle, "roles": ["data"]}
        # The manifest's recorded schema rides the data asset, as on a companion table.
        if meta.get("columns"):
            data["table:columns"] = meta["columns"]
        assets, exts = with_related(label, data)
        links = []
        # Tiles carry the same asset + web-map link as a serving topic's, so the viewer previews
        # the layer and adds it to the map the same way. The item's `proj:*` is the layer's native
        # CRS; the tiles state their own, which overrides it.
        if meta.get("pmtiles_layer"):
            pm_url = config.public_url(pmtiles_object(sid.upper(), label))
            assets["pmtiles"] = {**stac.pmtiles_asset(pm_url), "proj:code": "EPSG:4326"}
            links = [stac.pmtiles_link(pm_url, [meta["pmtiles_layer"]])]
            exts += [stac.WEB_MAP_LINKS_EXT, stac.PROJ_EXT]
            if meta.get("style"):  # restyle binds this file; an ugs-styles style for the Item wins
                generated = config.public_url(f"{VECTORS_PREFIX}/{sid.upper()}/{meta['style']}")
        items.append(item(ids[label], ltitle, assets,
            geometry=stac.bbox_polygon(lb) if lb else aoi, item_bbox=lb or aoi_bbox,
            footprint_source=LAYER_BBOX_SOURCE if lb else aoi_source,
            extra={**crs_of(meta), **rows, **({"description": ldesc} if ldesc else {}),
                   **({stac.GENERATED_STYLE: generated} if generated else {}),
                   **kind_props(label, True)},
            links=[*links, *rel_links.get(label, [])], extensions=exts or None))
    for t in companion_tables or []:
        label, meta = t["label"], layer_meta.get(t["label"], {})
        ltitle, ldesc = titles[label]
        asset = {"href": config.public_url(f"{VECTORS_PREFIX}/{sid.upper()}/{label}.parquet"),
                 "type": PARQUET_MIME, "title": ltitle, "roles": ["data"]}
        if t.get("columns"):
            asset["table:columns"] = t["columns"]
        assets, exts = with_related(label, asset)
        items.append(item(ids[label], ltitle, assets,
                          geometry=aoi, item_bbox=aoi_bbox,
                          footprint_source=TABLE_FOOTPRINT_SOURCE if geom else FOOTPRINT_FALLBACK,
                          extra={**({"table:row_count": meta["rows"]} if meta.get("rows") is not None else {}),
                                 **({"description": ldesc} if ldesc else {}),
                                 **kind_props(label, False)},
                          links=rel_links.get(label), extensions=exts or None))
    if has_3d:
        items.append(item(f"{pub_id}_fence", "3D fence diagram", {
            "fence_polygons": {"href": config.public_url(threed_object(sid, POLY_NAME)), "type": PARQUET_MIME,
                               "roles": ["data", "3d-vector"], "title": "3D fence polygons (GeoParquet)"},
            "fence_lines": {"href": config.public_url(threed_object(sid, LINE_NAME)), "type": PARQUET_MIME,
                            "roles": ["data", "3d-vector"], "title": "3D fence contacts & faults (GeoParquet)"},
            "fence_mesh": {"href": config.public_url(threed_object(sid, MESH_NAME)), "type": "model/gltf-binary",
                           "roles": ["data", "visual"], "title": "3D fence mesh (glTF)"}},
            geometry=aoi, item_bbox=aoi_bbox, footprint_source=aoi_source,
            extra={"classification:classes": classes_3d} if classes_3d else None,
            extensions=[stac.CLASSIFICATION_EXT] if classes_3d else None))

    seen: set[str] = set()
    dupes = sorted({it["id"] for it in items
                    if it["id"].casefold() in seen or seen.add(it["id"].casefold())})
    if dupes:
        raise ValueError(f"{pub_id}: duplicate item ids {dupes}")

    single = is_single_file(has_cog=has_cog, has_units=has_units, has_3d=has_3d,
                            vector_layers=vector_layers, companion_tables=companion_tables)
    if single != (len(items) == 1):
        raise ValueError(f"{pub_id}: single-file rule and built items disagree ({len(items)} items)")
    # Only real extents widen the Collection's: an Item placed on the Utah placeholder adds nothing.
    item_bboxes = [to_2d_bbox(it["bbox"]) for it in items if it.get("bbox")
                   and it["properties"].get("ugs:footprint_source") != FOOTPRINT_FALLBACK]
    data_assets: dict = {}
    style_fields: dict = {}
    data_exts: list[str] = []
    moved_links: list[dict] = []
    if single:
        # The lone Item's data moves to the Collection; its item-level CRS, row count and class
        # fields are only valid on an asset there, so they ride each data asset (not the tiles, which
        # are in EPSG:4326). Its tiles' web-map link moves with it.
        only = items.pop()
        carried = {k: v for k, v in only["properties"].items()
                   if k.startswith(("proj:", "table:", "classification:"))}
        data_assets = {k: {**a, **{ck: cv for ck, cv in carried.items()
                                   if ck not in a and "data" in a.get("roles", [])}}
                       for k, a in only["assets"].items() if k != "thumbnail"}
        data_exts = [e for e in only.get("stac_extensions", []) if e != stac.SCI_EXT]
        moved_links = [*mosaic, *(lk for lk in only["links"] if lk.get("rel") == "pmtiles")]
        if only["properties"].get(stac.GENERATED_STYLE):
            style_fields = {stac.GENERATED_STYLE: only["properties"][stac.GENERATED_STYLE]}

    assets: dict = {}
    main_pdf = href(p.get("pub_url"))
    if main_pdf:
        mtype = media_type(main_pdf)
        assets["publication"] = _source_asset(main_pdf, mirrored, type=mtype, title="Publication",
                                              roles=_file_roles(mtype))
    pairs = [(h, a.get("extra_data")) for a in attachments if (h := href(a.get("pub_url")))]
    keys, labels = identity.attachment_keys(pairs), identity.attachment_labels(pairs)
    done: set[str] = set()
    for a in attachments:
        h = href(a.get("pub_url"))
        if not h or h in done:
            continue
        done.add(h)
        mtype = media_type(h)
        assets[keys[h]] = _source_asset(h, mirrored, type=mtype,
                                        title=(labels[h] or "").strip(),
                                        roles=_file_roles(mtype))
    for key, asset in data_assets.items():
        new = key
        n = 1
        while new.casefold() in {k.casefold() for k in assets} or new in ("thumbnail", "overview"):
            n += 1
            new = f"{key}_{n}"
        if new != key:
            print(f"[pubs] {pub_id}: data asset {key!r} renamed to {new!r} (collides with a document asset)",
                  file=sys.stderr)
        assets[new] = asset
    if geopackage and geopackage.get("bytes"):
        assets["geopackage"] = {
            "href": config.public_url(geopackage_object(sid.upper())), "type": GEOPACKAGE_MIME,
            "roles": ["data"], "title": "All data (GeoPackage)",
            **stac.file_fields(gcs.FileMeta(geopackage["bytes"], geopackage.get("checksum")))}
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

    boxes = [*([to_2d_bbox(bbox)] if bbox else []), *item_bboxes]
    ext_bbox = stac.union_bbox(boxes) or stac.UTAH_BBOX
    extent = {"spatial": {"bbox": [ext_bbox]},
              "temporal": {"interval": [[dt, dt] if dt else [EARLIEST_RECORD, now]]}}
    links = [{"rel": "alternate", "href": f"{LANDING}{quoted_sid}", "type": "text/html",
              "title": "UGS publication landing page"}, *doi, *moved_links]
    for rel, key in (("predecessor-version", "predecessor_href"), ("successor-version", "successor_href"),
                     ("latest-version", "latest_href")):
        if ed.get(key):
            links.append({"rel": rel, "href": ed[key], "type": "application/json"})
    # Falls back to the old-layout item's description.
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
        # No footprint and no Item with a real bbox: the extent is the Utah placeholder, and says so.
        **({"ugs:footprint_source": FOOTPRINT_FALLBACK} if not boxes
           else {"ugs:footprint_source": fp_source} if fp_source else {}),
        **({"keywords": kw} if (kw := keywords_of(p.get("keywords"))) else {}),
        **({"ugs:county": cty} if (cty := counties.county_of_pub(p)) else {}),
        **({"ugs:volume": vol} if (vol := issue_volume(sid)) is not None else {}),
        **({"ugs:contents": contents} if contents else {}),
        **sci,
        **style_fields,
    }
    coll_exts = list(dict.fromkeys([
        *([stac.SCI_EXT] if sci else []),
        *([stac.ALTERNATE_ASSETS_EXT] if any("alternate" in a for a in assets.values()) else []),
        *data_exts]))
    coll = stac.build_collection(
        collection_id=pub_id, collection_path=coll_path, title=title,
        description=desc or title, extent=extent, assets=assets,
        item_titles={it["id"]: it["properties"]["title"] for it in items},
        item_link_fields={it["id"]: {k: it["properties"][k] for k in ITEM_LINK_FIELDS
                                     if k in it["properties"]} for it in items},
        extra_links=links, fields=fields, stac_extensions=coll_exts or None)
    return coll, items


# Item properties a publication's rel=item links repeat, so the viewer groups its items unfetched.
ITEM_LINK_FIELDS = ("ugs:layer_kind", "ugs:source_key", "ugs:source_dataset", "ugs:source_file",
                    "ugs:geometry_kind", "table:row_count")
_GEOMETRY_WORD = {"point": "points", "line": "lines", "polygon": "polygons"}


def geometry_kind(types: list[str] | None) -> str | None:
    """The plain word for a layer's manifest `geometry_types`: points, lines, polygons, or mixed
    when they span several. None when the manifest records none or one this does not know."""
    words = set()
    for t in types or []:
        base = re.sub(r"(zm|z|m)$", "", re.sub(r"\s+", "", str(t)).lower())
        base = base.removeprefix("multi").replace("string", "")
        if base not in _GEOMETRY_WORD:
            return None
        words.add(_GEOMETRY_WORD[base])
    return None if not words else words.pop() if len(words) == 1 else "mixed"
# A related table that many of the pub's map layers point at (DataSources on a GeMS map) is a
# lookup shared across the map rather than one layer's companion; its assets carry this role.
REFERENCE_ROLE = "reference"


def _relationship_parts(sid: str, coll_path: str, relationships: list[dict], ids: dict[str, str],
                        titles: dict[str, tuple[str, str | None]],
                        table_columns: dict[str, list | None], spatial: set[str],
                        unpublished: set[str], row_counts: dict[str, int] | None = None
                        ) -> tuple[dict, dict, dict, list[dict]]:
    """The relationships as STAC pieces, per label: ({origin: {destination: related-table asset}},
    {label: [related links]}, {origin: [own foreign keys]}, [relationships published]). A
    related-table asset's foreign key is flipped to destination -> origin, as vector/related.py
    `_inverted_rel` does, so the viewer's click-join filters the table by the clicked feature. A
    relationship to a validation or empty layer (`unpublished`) is skipped; one naming any other
    label not published raises."""
    assets: dict[str, dict] = {}
    published: list[dict] = []
    links: dict[str, list[dict]] = {}
    fks: dict[str, list[dict]] = {}

    def parquet(label: str) -> str:
        return config.public_url(f"{VECTORS_PREFIX}/{sid.upper()}/{label}.parquet")

    def link(frm: str, to: str) -> None:
        new = stac.related_link(coll_path, ids[to], titles[to][0])
        if all(lk["href"] != new["href"] for lk in links.get(frm, [])):
            links.setdefault(frm, []).append(new)

    for r in relationships:
        o, d = r["origin"], r["destination"]
        missing = [end for end in (o, d) if end not in ids]
        if missing and all(end in unpublished for end in missing):
            continue
        if missing or o == d:
            raise ValueError(f"{item_id_for(sid)}: relationship {o} -> {d} names "
                             f"{missing or [o]}, which is not a separate published layer or table")
        published.append(r)
        how = {"ugs:detected_by": r["detected_by"],
               **({"ugs:confidence": r["confidence"]} if r["detected_by"] == "inferred" else {})}
        fks.setdefault(o, []).append(stac.foreign_key(r["origin_cols"], ids[d], parquet(d),
                                                      r["destination_cols"], **how))
        if d in table_columns:
            fk = stac.foreign_key(r["destination_cols"], ids[o], parquet(o), r["origin_cols"], **how)
            existing = assets.setdefault(o, {}).get(d)
            if existing is not None:
                existing["ugs:foreign_keys"].append(fk)
            else:
                assets[o][d] = {"href": parquet(d), "type": PARQUET_MIME, "roles": ["data", "related"],
                                "title": titles[d][0], "ugs:foreign_keys": [fk],
                                **({"table:columns": table_columns[d]} if table_columns[d] else {}),
                                **({"table:row_count": (row_counts or {})[d]}
                                   if (row_counts or {}).get(d) is not None else {})}
        else:
            link(o, d)
        link(d, o)
    by_layers: dict[str, set[str]] = {}
    for r in published:
        if r["origin"] in spatial:
            by_layers.setdefault(r["destination"], set()).add(r["origin"])
    shared = {d for d, origins in by_layers.items() if len(origins) >= max(3, math.ceil(len(spatial) / 2))}
    for tables in assets.values():
        for d, asset in tables.items():
            if d in shared:
                asset["roles"].append(REFERENCE_ROLE)
    return assets, links, fks, published
