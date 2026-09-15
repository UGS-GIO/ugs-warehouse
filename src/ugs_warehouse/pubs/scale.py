"""Publication scale → serving scale-tier (24k/250k/500k). Shared by geolmap_mosaics, editions, and sink_stac."""
from __future__ import annotations

import re

SCALE_LABEL = {"24k": "1:24,000", "250k": "1:250,000", "500k": "1:500,000"}
DEFAULT_TIER = "24k"          # COG present but scale unparseable/blank -> finest tier (+ logged)


def _denominator(raw: str) -> int | None:
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
    m = re.search(r"1\s*in(?:ch)?\s*=\s*([\d.]+)\s*feet", s)
    if m:
        return int(float(m.group(1)) * 12)
    m = re.search(r"1\s*in(?:ch)?\s*=\s*([\d.]+)\s*mile", s)
    if m:
        return int(float(m.group(1)) * 63360)
    return None


def tier_of(raw: str) -> str | None:
    """Scale-tier key for a publication scale, or None when unparseable (caller applies fallback)."""
    d = _denominator(raw)
    if d is None or d <= 0:
        return None
    if d <= 62_500:
        return "24k"
    if d <= 350_000:
        return "250k"
    return "500k"
