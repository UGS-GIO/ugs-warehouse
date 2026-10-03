"""stac-geoparquet item mirror — one Parquet copy of a collection's items.

A client fetching a collection today makes one HTTP request per item. The mirror answers the same
question in one range request, and lets a consumer filter a collection spatially or temporally
without a STAC API. Every collection gets one; Portolan asks for it on rasters (`PTL-MIR-001`).
The item JSON stays the normative copy and this is derived from it on every refresh, so the two
cannot drift.

rustac writes it, in the stac-geoparquet layout: GeoParquet 1.1 with the bbox covering and the
item properties as top-level columns.
"""
from __future__ import annotations

import os
import tempfile

from . import config, gcs

OBJECT_NAME = "items.parquet"
ASSET_KEY = "items"


def object_path(collection_path: str) -> str:
    return f"{config.STAC_PREFIX}/{collection_path}/{OBJECT_NAME}"


def write(collection_path: str, items: list[dict]) -> gcs.FileMeta | None:
    """Publish `items.parquet` beside a collection.json. Returns what the upload reported, or None.

    Best-effort by design: the mirror is derived data, and a refresh that cannot build it must
    still publish the collection. An item without geometry is skipped rather than written as a null
    row, since a mirror row that cannot be queried spatially is worse than an absent one.
    """
    spatial = [it for it in items if it.get("geometry") and it.get("bbox")]
    if not spatial:
        return None
    try:
        import rustac

        with tempfile.TemporaryDirectory() as tmp:
            parquet = os.path.join(tmp, OBJECT_NAME)
            rustac.write_sync(parquet, [{k: v for k, v in it.items() if not k.startswith("_")}
                                        for it in spatial], format="geoparquet")
            return gcs.upload(parquet, object_path(collection_path),
                              content_type=config.PARQUET_MIME,
                              cache_control=gcs.CACHE_MUTABLE)
    except Exception as e:  # noqa: BLE001 — derived data; never sink the refresh that publishes the collection
        print(f"[item-mirror] {collection_path}: SKIP ({e})")
        return None


def asset(collection_path: str, meta: gcs.FileMeta | None) -> dict:
    """The collection-level asset that registers the mirror.

    That registration is the whole requirement — the spec defines no `rel:"items"` link for it.
    """
    from . import stac

    if meta is None:
        return {}
    return {ASSET_KEY: {"href": config.public_url(object_path(collection_path)),
                        "type": config.PARQUET_MIME,
                        "roles": ["collection-mirror"],
                        "title": "Item mirror (stac-geoparquet)",
                        **stac.file_fields(meta)}}
