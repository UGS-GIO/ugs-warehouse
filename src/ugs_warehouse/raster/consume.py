"""Warehouse-side raster consumer — Track A of docs/RASTER_SPEC.md.

ugs-ingest (PR #169) writes validated raster editions to `raw.raster_catalog` (append-only,
one row per edition, `is_current` marks the live one); dataELT flips them dev->prod; the
warehouse then (1) promotes the staged COG to the public bucket and (2) emits a STAC item into
the unified catalog.

These functions take a `record` dict in the CONTRACT shape below — NOT the raw `raw.raster_catalog`
row. A caller must SELECT the row (by `item_id`) and ALIAS/transform ingest's stored columns into
these keys. That fetch layer + the promote Pub/Sub trigger are NOT built yet (message shape is still
open — #169 spec §8.3), so `promote()` currently has no production caller; it runs only from tests.

Contract key  ← ingest column (transform the fetch layer must apply)
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

from ..core import config, gcs
from . import sink_stac
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


def _properties(record: dict) -> dict:
    """STAC properties from the record, dropping empty values (e.g. null `units`)."""
    return {stac_key: record[col] for col, stac_key in _PROP_MAP.items()
            if record.get(col) not in (None, "")}


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


def promote(record: dict) -> str:
    """Promote a staged COG -> public bucket, then write the STAC item. Returns the item path.

    Copies `staged_cog_uri` (gs://stagedrasters/...) to `cog/<layer>/<item_id>.cog.tif` and,
    when `has_thumbnail`, its sibling `.thumb.png`. Needs read on the staged bucket (granted per
    the #169 contract). The caller runs `core.stac.refresh_catalog()` after a batch.
    """
    raster = raster_from_record(record)
    staged_cog = record["staged_cog_uri"]
    gcs.copy_from_uri(staged_cog, raster.cog_object_path,
                      content_type=config.COG_MIME, cache_control=gcs.CACHE_IMMUTABLE)
    # Thumbnail source isn't a distinct contract column — derive the staged sibling. Best-effort so
    # a missing thumb doesn't fail the promote; if it doesn't land, the item still advertises it.
    # TODO(marshall): confirm a `staged_thumb_uri` column vs the `.cog.tif`->`.thumb.png` sibling.
    if record.get("has_thumbnail") and staged_cog.endswith(".cog.tif"):
        try:
            gcs.copy_from_uri(staged_cog[:-len(".cog.tif")] + ".thumb.png",
                              raster.thumb_object_path,
                              content_type="image/png", cache_control=gcs.CACHE_IMMUTABLE)
        except Exception:  # noqa: BLE001 — no staged thumb → skip, item asset href just 404s until fixed
            pass
    return sink_stac.write(
        raster, bbox=record["bbox"], geometry=record.get("geometry"),
        properties=_properties(record), has_thumbnail=bool(record.get("has_thumbnail")),
        proj_epsg=record.get("epsg"),
    )
