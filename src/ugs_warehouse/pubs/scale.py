"""Publication scale bands (`tier_of`, the editions fallback) and the mosaic tiers, which are the
geologic map portal's layers (`mosaic_tier_of`). Shared by geolmap_mosaics, editions, sink_stac and ingest."""
from __future__ import annotations

import re

SCALE_LABEL = {"24k": "1:24,000", "250k": "1:250,000", "500k": "1:500,000"}
DEFAULT_TIER = "24k"          # COG present but scale unparseable/blank -> finest tier (+ logged)

_GROUPED = re.compile(r"\d{1,3}(?:[,. ]\d{3})+")   # 24,000 / 24 000 / 62.500
_INCHES_PER = {"feet": 12, "foot": 12, "ft": 12, "'": 12, "mile": 63_360, "miles": 63_360}
_RATIO = re.compile(r"\b1\s*:\s*(\d[\d,. ]*\d|\d)")
_VERBAL = re.compile(r"(?<![\d./])([\d./]+)\s*(?:inch(?:es)?|in\.?|\")\s*=\s*([\d,. ]+?)\s*"
                     r"(feet|foot|ft|'|miles?)(?!\w)")


def _number(s: str) -> float | None:
    """'24,000', '62.500' (dot-grouped), '1/4' or '2.5' as a number; None for anything else."""
    s = s.strip()
    if _GROUPED.fullmatch(s):
        return float(re.sub(r"[,. ]", "", s))
    if m := re.fullmatch(r"(\d+)/(\d+)", s):
        return int(m[1]) / int(m[2]) if int(m[2]) else None
    return float(s) if re.fullmatch(r"\d+(?:\.\d+)?", s) else None


def denominator(raw: str) -> int | None:
    """Free-text publication scale -> 1:N denominator, or None if unparseable.

    Reads '1:24,000' (several ratios give the largest scale), '24000', '100k', and verbal scales
    like '1 inch = 200 feet', 1" = 20', '1/4 inch = 1 foot' or '2 inches = 1 mile'. 10,000 feet
    or more to the inch is read as a mistyped ratio: the maps that say so are 7.5' quadrangles."""
    s = (raw or "").strip().lower()
    if ratios := _RATIO.findall(s):
        nums = [n for r in ratios if (n := _number(r))]
        return int(min(nums)) if len(nums) == len(ratios) else None
    if (n := _number(s)) is not None:
        return int(n) if n >= 1 else None   # 0.18…: a spreadsheet time of day, not a scale
    if m := re.fullmatch(r"(\d+)\s*k", s):
        return int(m[1]) * 1000
    if not (m := _VERBAL.search(s)):
        return None
    inches, n, per = _number(m[1]), _number(m[2]), _INCHES_PER[m[3]]
    if not inches or not n:
        return None
    if per == 12 and inches == 1 and n >= 10_000:
        return int(n)
    return round(n * per / inches) or None


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
