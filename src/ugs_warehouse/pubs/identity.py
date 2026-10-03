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
from urllib.parse import quote, unquote

# Object prefixes for the pubs producer (override via env). STAC items go to the shared
# core.config.STAC_PREFIX (the one catalog); these are the pub *data* artifacts.
COG_PREFIX = os.environ.get("GEOLMAP_COG_PREFIX", "geolmap/cogs")
FOOTPRINTS_PREFIX = os.environ.get("GEOLMAP_FOOTPRINTS_PREFIX", "geolmap/footprints")
UNITS_PREFIX = os.environ.get("GEOLMAP_UNITS_PREFIX", "geolmap/units")
# Cloud-native 3D fence-diagram artifacts (GeoParquet-3D polys/lines + glTF mesh + classes sidecar),
# converted from a pub's CSA_3D GeMS gdb + .mapx. Presence here drives the STAC 3D-asset stamping.
THREED_PREFIX = os.environ.get("GEOLMAP_3D_PREFIX", "geolmap/3d")
# Cover thumbnails (PDF first page) for ANY pub — spatial or not (Survey Notes, reports, …).
PUB_THUMB_PREFIX = os.environ.get("PUB_THUMB_PREFIX", "pubs/thumbs")
# Issue table-of-contents sidecars (Survey Notes "In this issue"), parsed from the PDF.
PUB_CONTENTS_PREFIX = os.environ.get("PUB_CONTENTS_PREFIX", "pubs/contents")
# Per-issue full-text search sidecars (article text by TOC page range); aggregated into one corpus.
PUB_SEARCH_PREFIX = os.environ.get("PUB_SEARCH_PREFIX", "pubs/search")
# Per-pub whole-document text (one .txt per publication) — source for the all-pub full-text index.
PUB_FULLTEXT_PREFIX = os.environ.get("PUB_FULLTEXT_PREFIX", "pubs/fulltext")
# Per-pub cached chunk embeddings (one .npz per publication). Pubs are immutable, so a pub is embedded
# once and reused on every later VSS rebuild — only NEW pubs get embedded. See pubs/embed.py.
PUB_EMB_PREFIX = os.environ.get("PUB_EMB_PREFIX", "pubs/embeddings")
# Per-scale seamless RASTER mosaics of the published geologic maps (the old ArcGIS MD_500K/250K/24K
# equivalent), as raster PMTiles — one per scale tier. Built by pubs/geolmap_mosaics.py from the COGs.
MOSAIC_PREFIX = os.environ.get("GEOLMAP_MOSAIC_PREFIX", "geolmap/mosaics")

# The legacy host every publication file is served from — the pubs database stores paths relative
# to it. Warehouse copies of those files (pubs/mirror.py) land under PUB_FILES_PREFIX.
UGSPUB = "https://ugspub.nr.utah.gov/publications/"
# Mirrored source files (PDFs, plate/GIS zips, tables). PATH-PRESERVING: the URL's path under
# /publications/ becomes the object path, so URL→object is a pure function and needs no index —
# `ingest` can list this prefix once and know exactly which assets it holds a copy of (#120).
PUB_FILES_PREFIX = os.environ.get("PUB_FILES_PREFIX", "pubs/files")


def pub_file_object(url: str) -> str | None:
    """Object path for the warehouse copy of a legacy-hosted pub file — None if we don't mirror it.

    Percent-escapes are decoded so the object name reads like the file does (a URL with `%20` and
    one with a raw space mirror to the SAME object); callers re-encode when building a public URL.
    """
    u = (url or "").strip()
    if not u.startswith(UGSPUB):
        return None  # foreign host — theirs to serve, not ours to copy
    rest = unquote(u[len(UGSPUB):]).lstrip("/")
    # A query string means the path alone doesn't identify the bytes; `..` would escape the prefix.
    if not rest or "?" in rest or "#" in rest or ".." in rest.split("/"):
        return None
    return f"{PUB_FILES_PREFIX}/{rest}"


def pub_file_url(object_path: str) -> str:
    """Public CDN URL for a mirrored file — the object path re-encoded for use in an href."""
    from ..core import config
    return config.public_url(quote(object_path, safe="/"))


def pub_contents_object(series_id: str) -> str:
    return f"{PUB_CONTENTS_PREFIX}/{series_id.upper()}.json"


def pub_search_object(series_id: str) -> str:
    return f"{PUB_SEARCH_PREFIX}/{series_id.upper()}.json"


def pub_fulltext_object(series_id: str) -> str:
    return f"{PUB_FULLTEXT_PREFIX}/{series_id.upper()}.txt"

# Top-level catalog routing. UGS hosts third-party material it didn't author; split it into
# sibling collections so the UGS geologic catalog stays clean (nothing dropped, just grouped).
PUBLICATIONS_COLLECTION = "ugs-publications"             # UGS/UGMS-authored + USGS Utah maps
MINING_DISTRICT_COLLECTION = "ugs-mining-district-files"  # MD series — archived 3rd-party mine files
EXTERNAL_COLLECTION = "ugs-external"                     # foreign publishers UGS only hosts


# Previews are WebP (#372). The harvest writes a map's catalog thumbnail and the sheet the 3D viewer
# drapes next to its COG; the cover job writes a cover for every pub. The ingest finds them by these
# suffixes, so producers and catalog share one spelling.
COG_THUMB_SUFFIX = ".thumb.webp"
COG_SHEET_SUFFIX = ".sheet.webp"
COVER_SUFFIX = ".webp"
# PNG previews still in GCS: webp_backfill converts them, and the ingest warns about any it finds
# unconverted, because an item can only point at the WebP.
PNG_THUMB_SUFFIX = ".thumb.png"
PNG_COVER_SUFFIX = ".png"


def pub_cover_object(series_id: str) -> str:
    return f"{PUB_THUMB_PREFIX}/{series_id.upper()}{COVER_SUFFIX}"


@dataclass(frozen=True)
class Pub:
    series_id: str  # e.g. "M-299DM" — the STAC item id + artifact basename

    @property
    def id(self) -> str:
        return self.series_id

    @property
    def cog_object(self) -> str:
        return f"{COG_PREFIX}/{self.series_id}.cog.tif"

    @property
    def thumb_object(self) -> str:
        return f"{COG_PREFIX}/{self.series_id}{COG_THUMB_SUFFIX}"

    @property
    def sheet_object(self) -> str:
        return f"{COG_PREFIX}/{self.series_id}{COG_SHEET_SUFFIX}"

    @classmethod
    def parse(cls, series_id: str) -> "Pub":
        sid = (series_id or "").strip().upper()
        if not sid:
            raise ValueError("empty series_id")
        return cls(series_id=sid)
