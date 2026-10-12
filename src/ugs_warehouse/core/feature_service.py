"""The layer list the OGC API Features service serves, written by the catalog refresh.

The service (`featureserv/`) reads this one file at startup instead of crawling the catalog. Each
entry names a GeoParquet archive by its `gs://` path, which the service reads with its own
credentials, and `feature_id`, the stable key the transform gives every row.
"""
from __future__ import annotations

import json

from . import config, gcs
from .bbox import to_2d_bbox

OBJECT = f"{config.FEATURES_PREFIX}/collections.json"
ID_FIELD = "feature_id"


def _source(item: dict) -> str | None:
    """The `gs://` path of the item's GeoParquet asset, or None when it has none on our CDN."""
    base = config.public_url("")
    for asset in (item.get("assets") or {}).values():
        href = asset.get("href") or ""
        if asset.get("type") == config.PARQUET_MIME and href.startswith(base):
            return f"gs://{config.BUCKET}/{href.removeprefix(base)}"
    return None


def collection(item: dict) -> dict | None:
    """One served layer from a STAC item; None when the item has no GeoParquet to serve."""
    source = _source(item)
    if not source:
        return None
    props = item.get("properties") or {}
    bbox = item.get("bbox")
    return {"id": item["id"], "title": props.get("title") or item["id"],
            "description": props.get("description") or props.get("title") or item["id"],
            "keywords": list(props.get("keywords") or []),
            "bbox": to_2d_bbox(bbox) if bbox else None, "source": source, "id_field": ID_FIELD}


def write(items: list[dict]) -> int:
    """Write the layer list for `items`; returns how many layers it names."""
    layers = sorted(filter(None, map(collection, items)), key=lambda c: c["id"])
    gcs.put_bytes(json.dumps({"collections": layers}).encode(), OBJECT,
                  content_type="application/json", cache_control=gcs.CACHE_MUTABLE)
    return len(layers)
