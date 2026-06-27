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
# Cover thumbnails (PDF first page) for ANY pub — spatial or not (Survey Notes, reports, …).
PUB_THUMB_PREFIX = os.environ.get("PUB_THUMB_PREFIX", "pubs/thumbs")
# Issue table-of-contents sidecars (Survey Notes "In this issue"), parsed from the PDF.
PUB_CONTENTS_PREFIX = os.environ.get("PUB_CONTENTS_PREFIX", "pubs/contents")


def pub_contents_object(series_id: str) -> str:
    return f"{PUB_CONTENTS_PREFIX}/{series_id.upper()}.json"

# Top-level catalog routing. UGS hosts third-party material it didn't author; split it into
# sibling collections so the UGS geologic catalog stays clean (nothing dropped, just grouped).
PUBLICATIONS_COLLECTION = "ugs-publications"             # UGS/UGMS-authored + USGS Utah maps
MINING_DISTRICT_COLLECTION = "ugs-mining-district-files"  # MD series — archived 3rd-party mine files
EXTERNAL_COLLECTION = "ugs-external"                     # foreign publishers UGS only hosts


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
