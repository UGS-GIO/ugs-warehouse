"""Publication scale bands (`tier_of`, the editions fallback) and the mosaic tiers, which are the
geologic map portal's layers (`mosaic_tier_of`). Shared by geolmap_mosaics, editions, sink_stac and ingest."""
from __future__ import annotations

import re

SCALE_LABEL = {"24k": "1:24,000", "250k": "1:250,000", "500k": "1:500,000"}
DEFAULT_TIER = "24k"          # COG present but scale unparseable/blank -> finest tier (+ logged)


def denominator(raw: str) -> int | None:
    """Free-text publication scale -> 1:N denominator, or None if unparseable.

    Handles '1:24,000', '1:24 000', '1 inch = 200 feet' (x12), '1 inch = 1 mile' (x63360)."""
    s = (raw or "").strip().lower().replace(",", "")
    if not s:
        return None
    m = re.search(r"1\s*:\s*(\d[\d ]*\d|\d)", s)         # ratio; tolerate an internal space (24 000)
    if m:
        try:
            return int(m.group(1).replace(" ", ""))
        except ValueError:
            return None
    for pattern, per_inch in ((r"1\s*in(?:ch)?\s*=\s*([\d.]+)\s*feet", 12),
                              (r"1\s*in(?:ch)?\s*=\s*([\d.]+)\s*mile", 63360)):
        if m := re.search(pattern, s):
            try:
                return int(float(m.group(1)) * per_inch)
            except ValueError:   # "1 inch = . feet": digits-and-dots that are no number
                return None
    return None


def tier_of(raw: str) -> str | None:
    """Scale-tier key for a publication scale, or None when unparseable (caller applies fallback)."""
    d = denominator(raw)
    if d is None or d <= 0:
        return None
    if d <= 62_500:
        return "24k"
    if d <= 350_000:
        return "250k"
    return "500k"


# Mosaic tiers are the geologic map portal's layers, read from a map's footprint `geomaps_service`,
# not its publication scale: the intermediate layer mixes 1:50,000 to 1:125,000 30' x 60' maps,
# which no scale band separates from the 7.5' quads or the 1 x 2 degree sheets.
MOSAIC_TIERS = ("24k", "100k", "250k", "500k")
# Portal layer IDs, as the footprints record them in geomaps_service / servName.
SERVICE_TIER = {"geomaps_24k": "24k", "geomaps_100k": "100k", "geomaps_1x2": "250k"}
STATEWIDE_SERVNAME = "500k_Statewide"   # the state map carries no geomaps_service
MOSAIC_TIER_LABEL = {"24k": "1:24,000", "100k": "intermediate-scale (30' x 60')",
                     "250k": "1:250,000 (1 x 2 degree)", "500k": "1:500,000"}


def mosaic_tiers(services: frozenset[str], serv_names: frozenset[str]) -> set[str]:
    """Every mosaic tier a map's portal layer(s) (see `editions.layers_by_series`) point at."""
    tiers = {SERVICE_TIER[s] for s in services if s in SERVICE_TIER}
    if STATEWIDE_SERVNAME in serv_names:
        tiers.add("500k")
    return tiers


def mosaic_tier_of(services: frozenset[str], serv_names: frozenset[str]) -> str | None:
    """The map's mosaic tier, or None when it has no tiered layer (irregular maps, no footprint) or
    its footprints point at two tiers."""
    tiers = mosaic_tiers(services, serv_names)
    return next(iter(tiers)) if len(tiers) == 1 else None
