"""Warehouse-side raster consumer (see docs/RASTER.md).

ugs-ingest (PR #169) writes validated raster editions to `raw.raster_catalog` (append-only,
one row per edition, `is_current` marks the live one); dataELT flips them dev->prod; the
warehouse then (1) promotes the staged COG to the public bucket and (2) emits a STAC item into
the unified catalog.

Wiring (end-to-end): the raster promote message `{item_id}` → the `POST /raster` push handler
(`service/main.py`) → `consume(item_id)` → `source.fetch_record` SELECTs + aliases the row →
`promote()` copies the COG + emits STAC → `core.stac.refresh_catalog()`. `scripts/provision.sh`
creates the topic and the push subscription; the runtime SA's read on the staged bucket
(`gs://stagedrasters`) is not granted by anything in this repo.

`promote()`/`stac_item_from_record()` take a `record` dict in the CONTRACT shape below — the raw
`raw.raster_catalog` row aliased/transformed by `source.py` (never the raw row directly).

Contract key  ← ingest column (transform source.py applies)
  layer         ← layer (= domain_topic)                     [verbatim]
  item_id       ← item_id  (`{piece}_{pubid}_{pubdate}`)     [verbatim]
  collection    ← collection (`ugs-rasters/<layer>`)          [verbatim — the migration's
                    `ugs-raster-<layer>` COMMENT is stale; ingest identity.py writes the slash form]
  datetime      ← publication_date (date, never null)         [→ ISO 8601 string]
  bbox          ← bbox_4326 (`[w,s,e,n]`)                     [verbatim]
  geometry      ← footprint_geom (PostGIS)                    [→ GeoJSON dict, e.g. ST_AsGeoJSON]
  epsg          ← native_crs (TEXT, e.g. "EPSG:26912")        [→ int 26912; NOT already an int]
  staged_cog_uri← staged_cog_uri (canonical native COG)       [verbatim]
  title, description, data_type, units, ugs_author, ugs_pub_type, has_thumbnail  [verbatim]
Grouping/versioning columns (`piece_id`, `pub_id`, `is_mosaic`, `is_current`) govern DB/promote
state, not the item body.
"""
from __future__ import annotations

from ..core import config, gcs, stac
from . import sink_stac, source
from .identity import Raster

# raw.raster_catalog column -> STAC property key. snake_case columns map to the ugs: namespace.
_PROP_MAP = {
    "title": "title",
    "description": "description",
    "data_type": "data_type",
    "units": "units",              # nullable — pixel-value unit; null for scanned maps
    "ugs_author": "ugs:author",
    "ugs_pub_type": "ugs:pub_type",
    "pub_id": "ugs:pub_id",        # source publication (OFR-123 …) — provenance + discovery
    "is_mosaic": "ugs:is_mosaic",  # topic type: seamless mosaic vs single COG
}


def raster_from_record(record: dict) -> Raster:
    """raw.raster_catalog row -> Raster identity (item_id/collection are ingest-authored)."""
    return Raster(
        layer=record["layer"],
        item_id=record["item_id"],
        collection=record["collection"],
        datetime_iso=record["datetime"],
    )


# STAC types `data_type` as a closed enum of pixel types, with `other` as the escape hatch. The
# producer sends what the raster means rather than how it is stored ("categorical"), which is a
# useful thing to say and not a value this field can hold — both raster items failed pystac and
# rashid on it (#255). The reserved name gets a legal value; the producer's word survives beside it.
_STAC_DATA_TYPES = {
    "int8", "int16", "int32", "int64", "uint8", "uint16", "uint32", "uint64",
    "float16", "float32", "float64", "cint16", "cint32", "cfloat32", "cfloat64", "other",
}


def _properties(record: dict) -> dict:
    """STAC properties from the record, dropping empty values (e.g. null `units`).

    Values arrive from the promote message and are published as-is, so a reserved STAC name has to
    be checked here: an upstream value outside its enum makes the item fail validation for every
    consumer, with no warehouse code being wrong.
    """
    props = {stac_key: record[col] for col, stac_key in _PROP_MAP.items()
             if record.get(col) not in (None, "")}
    declared = props.get("data_type")
    if declared is not None and declared not in _STAC_DATA_TYPES:
        props["data_type"] = "other"
        props["ugs:data_type"] = declared
        print(f"[{record.get('item_id')}] data_type {declared!r} is not a STAC pixel type; "
              f"published as 'other' with the original on ugs:data_type")
    return props


def stac_item_from_record(record: dict) -> dict:
    """Build (don't upload) the STAC item for a catalog record — pure, testable."""
    return sink_stac.build_item(
        raster_from_record(record),
        bbox=record["bbox"],
        geometry=record.get("geometry"),
        properties=_properties(record),
        has_thumbnail=bool(record.get("has_thumbnail")),
        proj_epsg=record.get("epsg"),
    )


def _staged_source(uri: object) -> str:
    """Return `uri` if it names an allowlisted staging bucket, else raise.

    Whatever this points at gets copied into the CDN-served bucket, and it's a catalog value —
    so which bucket we'll read from is config, not something the row gets to decide.
    """
    if not isinstance(uri, str) or not uri.startswith("gs://"):
        raise ValueError(f"staged_cog_uri must be a gs:// URI; got {uri!r}")
    bucket = uri[len("gs://"):].partition("/")[0]
    if bucket not in config.STAGED_SOURCE_BUCKETS:
        raise ValueError(
            f"staged_cog_uri bucket {bucket!r} is not in WAREHOUSE_STAGED_SOURCE_BUCKETS "
            f"{config.STAGED_SOURCE_BUCKETS}"
        )
    return uri


def promote(record: dict) -> str:
    """Promote a staged COG -> public bucket, then write the STAC item. Returns the item path.

    Copies `staged_cog_uri` (gs://stagedrasters/...) to `cog/<layer>/<item_id>.cog.tif` and,
    when `has_thumbnail`, its sibling `.thumb.png`. Needs read on the staged bucket (granted per
    the #169 contract). The caller runs `core.stac.refresh_catalog()` after a batch.
    """
    raster = raster_from_record(record)
    staged_cog = _staged_source(record["staged_cog_uri"])
    # Both copies are server-side rewrites, so each reports a size and no checksum (core.gcs).
    file_meta = {"cog": gcs.copy_from_uri(staged_cog, raster.cog_object_path,
                                          content_type=config.COG_MIME,
                                          cache_control=gcs.CACHE_IMMUTABLE)}
    # The web-mercator derivative ingest stages beside the canonical COG (#84). It is what a web map
    # can actually draw, so the item advertises `visual` only when this copy lands — a `visual` the
    # client cannot reproject is worse than none, which is the error the viewer was throwing.
    if staged_cog.endswith(".cog.tif"):
        try:
            file_meta["visual"] = gcs.copy_from_uri(
                staged_cog[:-len(".cog.tif")] + "_3857.cog.tif",
                raster.webmercator_cog_object_path,
                content_type=config.COG_MIME, cache_control=gcs.CACHE_IMMUTABLE)
        except Exception as e:  # noqa: BLE001 — not staged yet → item keeps the native COG as data only
            print(f"[{raster.item_id}] no web-mercator COG staged ({e}); item advertises no visual asset")

    # Thumbnail source isn't a distinct contract column — derive the staged sibling. Best-effort so
    # a missing thumb doesn't fail the promote; if it doesn't land, the item still advertises it.
    # TODO(marshall): confirm a `staged_thumb_uri` column vs the `.cog.tif`->`.thumb.png` sibling.
    if record.get("has_thumbnail") and staged_cog.endswith(".cog.tif"):
        try:
            file_meta["thumbnail"] = gcs.copy_from_uri(
                staged_cog[:-len(".cog.tif")] + ".thumb.png", raster.thumb_object_path,
                content_type="image/png", cache_control=gcs.CACHE_IMMUTABLE)
        except Exception:  # noqa: BLE001 — no staged thumb → skip, item asset href just 404s until fixed
            pass
    return sink_stac.write(
        raster, bbox=record["bbox"], geometry=record.get("geometry"),
        properties=_properties(record), has_thumbnail=bool(record.get("has_thumbnail")),
        proj_epsg=record.get("epsg"), has_webmercator="visual" in file_meta,
        file_meta=file_meta,
    )


def consume(item_id: str) -> str | None:
    """Trigger entry point (Pub/Sub promote → this): fetch the edition from raw.raster_catalog,
    promote its COG + emit the STAC item, then refresh the catalog. Returns the item path, or None if
    no such `item_id` (e.g. the edition isn't promoted to prod yet) so the caller can ack + skip.
    Raises ValueError on a malformed item_id."""
    record = source.fetch_record(item_id)
    if record is None:
        return None
    path = promote(record)
    stac.refresh_catalog()  # rebuild collection.json/catalog.json to include the new item
    return path
