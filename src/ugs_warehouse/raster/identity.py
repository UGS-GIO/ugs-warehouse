"""Identity for the raster producer — the COG/STAC analog of pubs `series_id`.

Per the ugs-ingest #169 contract (append-only editions): every layer is a versioned
collection `ugs-rasters/<layer>` of dated Items — one row/edition in `raw.raster_catalog`,
`is_current` marking the live one, nothing overwriting. `item_id` and `collection` are
emitted by ingest (`{piece}_{pubid}_{pubdate}`); the warehouse owns only the COG path
convention (`cog/<layer>/<item_id>.*`, matching the other artifact sinks + #41).
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from ..core import identifiers

# Raster *data* artifacts (the STAC catalog itself lives under core.config.STAC_PREFIX).
# `cog/` matches the other artifact-type sinks (pmtiles/, geoparquet/, thumbs/, stac/) and the path
# ugs-ingest #169 writes. Layout: <prefix>/<layer>/<item_id>.*
COG_PREFIX = os.environ.get("WAREHOUSE_RASTER_COG_PREFIX", "cog")


@dataclass(frozen=True)
class Raster:
    """One raster edition. `item_id`/`collection` come from the catalog row (ingest-authored);
    `datetime_iso` is the publication date and is never null under the append-only contract."""
    layer: str          # model / layer name (== ingest `domain_topic`), e.g. "slope"
    item_id: str         # unique per edition, e.g. "slope_OFR123_20260601"
    collection: str      # collection LAYOUT PATH, nested per #169, e.g. "ugs-rasters/slope"
    datetime_iso: str    # publication date (ISO 8601) — never None

    def __post_init__(self) -> None:
        """All three build GCS object paths below, and arrive from `raw.raster_catalog` — only
        `item_id` was checked, and only on the SQL path in `source.fetch_record`."""
        identifiers.require_token("raster layer", self.layer)
        identifiers.require_token("raster item_id", self.item_id)
        identifiers.require_object_path("raster collection", self.collection)

    @property
    def collection_id(self) -> str:
        """STAC collection id = the last path segment (the layer), like a pub series code.
        `collection` is the full layout path; core.stac nests by that path's depth."""
        return self.collection.rsplit("/", 1)[-1]

    @property
    def cog_object_path(self) -> str:
        """`cog/<layer>/<item_id>.cog.tif`."""
        return f"{COG_PREFIX}/{self.layer}/{self.item_id}.cog.tif"

    @property
    def webmercator_cog_object_path(self) -> str:
        """`cog/<layer>/<item_id>_3857.cog.tif` — the derivative a web map can actually draw."""
        return f"{COG_PREFIX}/{self.layer}/{self.item_id}_3857.cog.tif"

    @property
    def thumb_object_path(self) -> str:
        return f"{COG_PREFIX}/{self.layer}/{self.item_id}.thumb.png"
