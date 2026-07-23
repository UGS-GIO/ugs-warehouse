"""Warehouse-side raster consumer — Track A of docs/RASTER_SPEC.md.

ugs-ingest (PR #169) writes validated raster editions to `raw.raster_catalog` (append-only,
one row per edition, `is_current` marks the live one); dataELT flips them dev->prod; the
warehouse then (1) promotes the staged COG to the public bucket and (2) emits a STAC item into
the unified catalog.

Columns bound per the #169 contract (2026-07-23): identity — `layer`, `item_id`, `collection`,
`datetime` (publication_date, never null), `staged_cog_uri`; spatial — `bbox`, `geometry`
(footprint GeoJSON), `epsg` (native CRS); properties — `title`, `description`, `data_type`,
`units` (nullable), `ugs_author`, `ugs_pub_type`, `has_thumbnail`. Grouping/versioning columns
(`piece_id`, `pub_id`, `is_mosaic`, `is_current`) govern DB/promote state, not the item body.
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
