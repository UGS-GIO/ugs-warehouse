"""Shared GCS + CDN config — one bucket, one public base, per-artifact prefixes.

The raw bucket is **private**; the maps-assets CDN is the only public read surface and it
preserves the object path (bucket object `<prefix>/<file>` → `<CDN>/<prefix>/<file>`). Both
producers share this so artifacts land in one bucket and one catalog.
"""
from __future__ import annotations

import os

# GCS bucket all artifacts are written to (private; served publicly via the CDN).
BUCKET = os.environ.get("WAREHOUSE_BUCKET", "ut-dnr-ugs-maps-prod-public")

# Public CDN base — path-preserved, the only public read surface (browsers can't fetch
# gs://, and the bucket isn't public). Override only for a different CDN/host.
PUBLIC_BASE_URL = os.environ.get(
    "WAREHOUSE_PUBLIC_BASE_URL",
    "https://maps-assets.geology.utah.gov",
).rstrip("/")

# One STAC prefix — a single catalog spans all producers; collections live under it as
# `<STAC_PREFIX>/<collection>/<id>/<id>.json`, with `<STAC_PREFIX>/catalog.json` the root.
STAC_PREFIX = os.environ.get("WAREHOUSE_STAC_PREFIX", "warehouse/stac")

CATALOG_ID = os.environ.get("WAREHOUSE_CATALOG_ID", "ugs-warehouse")

# Per-artifact data prefixes (overridable). Vector producer defaults below; the pubs
# producer sets its own (e.g. geolmap/cogs) via its module config.
ARCHIVE_PREFIX = os.environ.get("WAREHOUSE_ARCHIVE_PREFIX", "warehouse/geoparquet")
PMTILES_PREFIX = os.environ.get("WAREHOUSE_PMTILES_PREFIX", "warehouse/pmtiles")
# Rendered preview thumbnails for vector serving-topics (styled PMTiles → PNG). One per stem,
# overwritten when its style changes (so CACHE_MUTABLE); a `.sha` sidecar holds the style hash
# the PNG was rendered from, for content-addressed skip-existing.
THUMBS_PREFIX = os.environ.get("WAREHOUSE_THUMBS_PREFIX", "warehouse/thumbs")

# Canonical media types for the cloud-native artifacts — one source of truth across all producers
# (was redefined in ~8 sink/harvest modules).
COG_MIME = "image/tiff; application=geotiff; profile=cloud-optimized"
PARQUET_MIME = "application/vnd.apache.parquet"
PMTILES_MIME = "application/vnd.pmtiles"

# OGC API Features endpoint (e.g., pg_featureserv base URL)
PGF_BASE_URL = os.environ.get(
    "PGF_BASE_URL",
    "https://api.geology.utah.gov",  # Replace with actual prod API URL
).rstrip("/")


def public_url(object_path: str) -> str:
    """CDN URL for a GCS object path (the CDN preserves the path)."""
    return f"{PUBLIC_BASE_URL}/{object_path.lstrip('/')}"


# Styling source — the neighbor repo `ugs-styles` builds MapLibre GL JSON + an `index.json`
# manifest, published CDN-only. The warehouse reads the manifest at STAC emit and attaches a
# `renders` block by item id (docs/STYLING.md). Graceful: unreachable manifest -> no renders.
STYLES_CDN_BASE = os.environ.get(
    "STYLES_CDN_BASE", f"{PUBLIC_BASE_URL}/styles",
).rstrip("/")
STYLES_INDEX_URL = os.environ.get("STYLES_INDEX_URL", f"{STYLES_CDN_BASE}/index.json")
