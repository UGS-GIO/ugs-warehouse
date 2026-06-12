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

# Per-artifact data prefixes (overridable). Vector producer defaults below; the pubs
# producer sets its own (e.g. geolmap/cogs) via its module config.
ARCHIVE_PREFIX = os.environ.get("WAREHOUSE_ARCHIVE_PREFIX", "warehouse/geoparquet")
PMTILES_PREFIX = os.environ.get("WAREHOUSE_PMTILES_PREFIX", "warehouse/pmtiles")

# OGC API Features endpoint (e.g., pg_featureserv base URL)
PGF_BASE_URL = os.environ.get(
    "PGF_BASE_URL",
    "https://api.geology.utah.gov",  # Replace with actual prod API URL
).rstrip("/")


def public_url(object_path: str) -> str:
    """CDN URL for a GCS object path (the CDN preserves the path)."""
    return f"{PUBLIC_BASE_URL}/{object_path.lstrip('/')}"
