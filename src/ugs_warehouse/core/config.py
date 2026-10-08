"""Shared GCS + CDN config — one bucket, one public base, per-artifact prefixes.

The raw bucket is **private**; the maps-assets CDN is the only public read surface and it
preserves the object path (bucket object `<prefix>/<file>` → `<CDN>/<prefix>/<file>`). Both
producers share this so artifacts land in one bucket and one catalog.
"""
from __future__ import annotations

import json
import os

# GCS bucket all artifacts are written to (private; served publicly via the CDN).
BUCKET = os.environ.get("WAREHOUSE_BUCKET", "ut-dnr-ugs-maps-prod-public")
# Where a producer READS existing warehouse artifacts (the geologic-map COGs and footprints) when it
# writes somewhere else: a review mosaic bake reads the public COGs but writes to the review bucket.
# Writes, and the catalog refresh, stay on BUCKET. An empty value means BUCKET, not a blank bucket.
SOURCE_BUCKET = os.environ.get("WAREHOUSE_SOURCE_BUCKET") or BUCKET

# Buckets a raster promote may copy FROM. `staged_cog_uri` is a catalog value, and whatever it
# names gets copied into BUCKET, which the CDN serves — so the source is allowlisted, not trusted.
STAGED_SOURCE_BUCKETS = tuple(
    b for b in os.environ.get("WAREHOUSE_STAGED_SOURCE_BUCKETS", "stagedrasters").split(",") if b
)

# Public CDN base — path-preserved, the only public read surface (browsers can't fetch
# gs://, and the bucket isn't public). Override only for a different CDN/host.
PUBLIC_BASE_URL = os.environ.get(
    "WAREHOUSE_PUBLIC_BASE_URL",
    "https://maps-assets.geology.utah.gov",
).rstrip("/")

# One STAC prefix — a single catalog spans all producers; collections live under it as
# `<STAC_PREFIX>/<collection>/<id>/<id>.json`, with `<STAC_PREFIX>/catalog.json` the root.
STAC_PREFIX = os.environ.get("WAREHOUSE_STAC_PREFIX", "warehouse/stac")

# True when this deploy writes the gated review catalog (the review build sets
# WAREHOUSE_STAC_PREFIX=review/stac, …), not the public one. Internal-only assets — e.g. the
# DuckLake locator, which no public consumer can read (gs:// + private bucket IAM, and a DuckLake
# table needs the private catalog DSN to resolve) — are stamped ONLY here. The public catalog must
# never advertise access the public can't have; the review app reads private assets via signed URLs.
IS_REVIEW_CATALOG = STAC_PREFIX.split("/", 1)[0] == "review"

# The public production catalog root. The review catalog links to this (rel=child) so the review
# app browses prod + review together WITHOUT duplicating prod artifacts — prod stays single-sourced
# on the public CDN. Fixed to that CDN + the default STAC prefix, so it holds even in a review deploy
# that swaps this process's PUBLIC_BASE_URL / STAC_PREFIX. Override only if the public catalog moves.
PUBLIC_CATALOG_URL = os.environ.get(
    "WAREHOUSE_PUBLIC_CATALOG_URL",
    "https://maps-assets.geology.utah.gov/warehouse/stac/catalog.json",
)

CATALOG_ID = os.environ.get("WAREHOUSE_CATALOG_ID", "ugs-warehouse")
# Without this a STAC Browser shows the bare id at the top of the tree.
CATALOG_TITLE = os.environ.get("WAREHOUSE_CATALOG_TITLE", "Utah Geological Survey data warehouse")

# External STAC catalogs federated under the warehouse root as rel=child — referenced,
# never copied, so each stays single-sourced on its own CDN (the same pattern the review
# catalog uses to link prod). (url, title) pairs. Override via WAREHOUSE_EXTERNAL_CATALOGS
# (JSON list of [url, title]). Default: the USWB (soil-water-balance) catalog.
EXTERNAL_CATALOGS: list[tuple[str, str]] = [
    tuple(pair) for pair in json.loads(  # type: ignore[misc]
        os.environ.get(
            "WAREHOUSE_EXTERNAL_CATALOGS",
            '[["https://ubm-assets.geology.utah.gov/stac/catalog.json", "UGS Soil Water Balance (USWB)"]]',
        )
    )
]

# Per-artifact data prefixes (overridable). Vector producer defaults below; the pubs
# producer sets its own (e.g. geolmap/cogs) via its module config.
ARCHIVE_PREFIX = os.environ.get("WAREHOUSE_ARCHIVE_PREFIX", "warehouse/geoparquet")
PMTILES_PREFIX = os.environ.get("WAREHOUSE_PMTILES_PREFIX", "warehouse/pmtiles")
# Rendered preview thumbnails for vector serving-topics (styled PMTiles → PNG). One per stem,
# overwritten when its style changes (so CACHE_MUTABLE); a `.sha` sidecar holds the style hash
# the PNG was rendered from, for content-addressed skip-existing.
THUMBS_PREFIX = os.environ.get("WAREHOUSE_THUMBS_PREFIX", "warehouse/thumbs")
# Hand-authored metadata overrides (description/title), one JSON per STAC item id, marked
# source=manual. Ingest prefers these over source metadata + they survive reingest — for backfilling
# fields the source doesn't carry reliably. Edited from the ops console.
OVERRIDES_PREFIX = os.environ.get("WAREHOUSE_OVERRIDES_PREFIX", "warehouse/overrides")
# The layer list the OGC API Features service (featureserv/) reads at startup, written by the
# catalog refresh.
FEATURES_PREFIX = os.environ.get("WAREHOUSE_FEATURES_PREFIX", "warehouse/featureserv")

# Canonical media types for the cloud-native artifacts — one source of truth across all producers
# (was redefined in ~8 sink/harvest modules).
COG_MIME = "image/tiff; application=geotiff; profile=cloud-optimized"
PARQUET_MIME = "application/vnd.apache.parquet"
PMTILES_MIME = "application/vnd.pmtiles"
WEBP_MIME = "image/webp"

# The deployed OGC API Features service (featureserv/). This is the Cloud Run hostname, not a
# vanity domain: the placeholder `api.geology.utah.gov` that used to sit here is NXDOMAIN, so every
# catalog document advertised a link that resolved nowhere. Deploy-specific by nature — override
# PGF_BASE_URL once a stable domain fronts the service, and republish the catalog.
PGF_BASE_URL = os.environ.get(
    "PGF_BASE_URL",
    "https://ugs-warehouse-features-xedvkyurga-uc.a.run.app",
).rstrip("/")


# License + attribution. Utah state geospatial data defaults to CC-BY-4.0 per UGRC policy
# (gis.utah.gov/documentation/policy/license) — an SPDX id, not the placeholder "proprietary".
# Override per-deployment if a given dataset carries different terms.
DATA_LICENSE = os.environ.get("WAREHOUSE_LICENSE", "CC-BY-4.0")  # SPDX identifier
LICENSE_URL = os.environ.get("WAREHOUSE_LICENSE_URL", "https://creativecommons.org/licenses/by/4.0/")

# STAC `providers` block — who produced/licenses/hosts the data. UGS is all three here.
PROVIDERS = [
    {"name": "Utah Geological Survey",
     "roles": ["producer", "processor", "licensor", "host"],
     "url": "https://geology.utah.gov"},
]


def public_url(object_path: str) -> str:
    """CDN URL for a GCS object path (the CDN preserves the path)."""
    return f"{PUBLIC_BASE_URL}/{object_path.lstrip('/')}"


def archive_path(stem: str) -> str:
    """The topic's latest GeoParquet pointer. Flat, so it resolves from the stem alone —
    unlike the item path, which also needs the target's mart schema."""
    return f"{ARCHIVE_PREFIX}/{stem}/{stem}.parquet"


# Styling source — the neighbor repo `ugs-styles` builds MapLibre GL JSON + an `index.json`
# manifest, published to OUR bucket and served through the CDN. The warehouse reads the manifest at
# STAC emit and attaches a `renders` block by item id (docs/STYLING.md). Graceful: unreachable
# manifest -> no renders.
STYLES_CDN_BASE = os.environ.get(
    "STYLES_CDN_BASE", f"{PUBLIC_BASE_URL}/styles",
).rstrip("/")
STYLES_INDEX_URL = os.environ.get("STYLES_INDEX_URL", f"{STYLES_CDN_BASE}/index.json")
# The manifest as a GCS object — the authoritative copy, of which STYLES_INDEX_URL is a CACHED view.
# ugs-styles' publish rsyncs here and then immediately triggers our rebind, so the CDN edge can
# still be serving the pre-publish manifest when we read it; binding that copy writes a stale legend
# onto every item we touch, and the job reports success while doing it. Read the object instead.
STYLES_INDEX_OBJECT = os.environ.get("STYLES_INDEX_OBJECT", "styles/index.json")
