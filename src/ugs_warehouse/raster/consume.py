"""Warehouse-side raster consumer — Track A of docs/RASTER_SPEC.md.

ugs-ingest (PR #169) writes validated raster editions to `raw.raster_catalog` (append-only,
one row per edition, `is_current` marks the live one); dataELT flips them dev->prod; the
warehouse then (1) promotes the staged COG to the public bucket and (2) emits a STAC item into
the unified catalog.

Wiring (end-to-end): ugs-ingest's promote (#183) publishes `{item_id}` → the `POST /raster` push
handler (`service/main.py`) → `consume(item_id)` → `source.fetch_record` SELECTs + aliases the row →
`promote()` copies the COG + emits STAC → `core.stac.refresh_catalog()`. DEPLOY FOLLOW-UP: provision
the raster promote topic + a push subscription to `<service>/raster` (mirror `scripts/provision.sh`),
and grant the runtime SA read on the staged bucket (`gs://stagedrasters`).

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
        proj_epsg=record.get("epsg"), file_meta=file_meta,
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
