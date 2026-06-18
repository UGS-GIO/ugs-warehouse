"""Related (aspatial) tables published as supporting assets on a serving-topic STAC item.

Some serving layers have companion tables that aren't geospatial — e.g. the UCRC wells layer
(`enmin_ucrc_wells`) has core boxes / photos / attachments, joined by `uwi`. They don't warrant
their own STAC items (no geometry), so the warehouse archives each to GeoParquet-less Parquet on
the CDN and earmarks it as a `roles:["data","related"]` asset on the parent item, carrying the
join key in `ugs:related_key`.

Source is the same Postgres `{schema}.{table}` the vector source reads (via DuckDB postgres
scanner). Best-effort: a missing table / DB error skips that asset, never sinks the parent ingest.
"""
from __future__ import annotations

import os
import tempfile

from ..core import config, gcs
from . import source
from .topics import Topic

PARQUET_MIME = "application/vnd.apache.parquet"

# Parent serving-topic stem -> its related aspatial tables.
#   asset  : STAC asset key on the parent item
#   schema : Postgres schema holding the `_current` table
#   table  : the `_current` table name
#   key    : the column that joins back to the parent layer
RELATED: dict[str, list[dict]] = {
    "enmin_ucrc_wells": [
        {"asset": "boxes", "schema": "energy_mineral",
         "table": "enmin_ucrc_boxes_current", "key": "uwi", "title": "UCRC core boxes"},
        {"asset": "photos", "schema": "energy_mineral",
         "table": "enmin_ucrc_photos_current", "key": "uwi", "title": "UCRC core photos"},
        {"asset": "attachments", "schema": "energy_mineral",
         "table": "enmin_ucrc_attachments_current", "key": "uwi", "title": "UCRC attachments"},
    ],
}


def _publish_one(con, rel: dict, parent_stem: str) -> dict | None:
    """Archive one related table to CDN Parquet; return its STAC asset (or None on failure)."""
    schema, table = rel["schema"], rel["table"]
    try:
        with tempfile.TemporaryDirectory() as tmp:
            local = os.path.join(tmp, f"{table}.parquet")
            con.execute(
                f'COPY (SELECT * FROM {source.PG_ALIAS}."{schema}"."{table}") '
                f"TO '{local}' (FORMAT PARQUET, COMPRESSION ZSTD)"
            )
            # Grouped under the parent's archive dir so related data lives with its layer.
            path = f"{config.ARCHIVE_PREFIX}/{parent_stem}/related/{rel['asset']}.parquet"
            gcs.upload(local, path, content_type=PARQUET_MIME, cache_control=gcs.CACHE_MUTABLE)
    except Exception as e:  # noqa: BLE001 — related data is best-effort; never sink the parent
        print(f"[{parent_stem}] related '{rel['asset']}' ({schema}.{table}) SKIP: {e}")
        return None
    return {
        "href": config.public_url(path),
        "type": PARQUET_MIME,
        "roles": ["data", "related"],
        "title": rel["title"],
        "ugs:related_key": rel["key"],
    }


def publish(topic: Topic) -> dict[str, dict]:
    """Publish every related table for `topic` and return `{asset_key: stac_asset}` to merge
    into the parent item's assets. Empty for topics with no related tables (the common case)."""
    rels = RELATED.get(topic.stem, [])
    if not rels:
        return {}
    con = source._connect()
    out: dict[str, dict] = {}
    for rel in rels:
        asset = _publish_one(con, rel, topic.stem)
        if asset:
            out[rel["asset"]] = asset
            print(f"[{topic.stem}] related: {asset['href']}")
    return out
