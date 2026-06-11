"""Pub identity — a UGS publication keyed by `series_id` (e.g. M-299DM).

The Topic analog for the pubs producer: `series_id` IS the STAC item id and the COG /
artifact basename. Metadata (title, type, scale, footprint, download links) is looked up
separately from the pubs source, mirroring how a vector `Topic` is just `{schema, layer}`.

Object prefixes for pub artifacts (all under one bucket, served via the CDN):
  cogs/<series_id>.cog.tif · footprints.parquet · units/<series_id>/*.parquet · stac/...
"""
from __future__ import annotations

import os
from dataclasses import dataclass

# Object prefixes for the pubs producer (override via env). STAC items go to the shared
# core.config.STAC_PREFIX (the one catalog); these are the pub *data* artifacts.
COG_PREFIX = os.environ.get("GEOLMAP_COG_PREFIX", "geolmap/cogs")
FOOTPRINTS_PREFIX = os.environ.get("GEOLMAP_FOOTPRINTS_PREFIX", "geolmap/footprints")
UNITS_PREFIX = os.environ.get("GEOLMAP_UNITS_PREFIX", "geolmap/units")

PUBLICATIONS_COLLECTION = "ugs-publications"


@dataclass(frozen=True)
class Pub:
    series_id: str  # e.g. "M-299DM" — the STAC item id + artifact basename

    @property
    def id(self) -> str:
        return self.series_id

    @property
    def cog_object(self) -> str:
        return f"{COG_PREFIX}/{self.series_id}.cog.tif"

    @classmethod
    def parse(cls, series_id: str) -> "Pub":
        sid = (series_id or "").strip().upper()
        if not sid:
            raise ValueError("empty series_id")
        return cls(series_id=sid)
