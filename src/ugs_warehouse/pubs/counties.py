"""Map a publication's point location to a Utah county — for the catalog county filter.

County isn't in any UGS pub source, so we derive it: point-in-polygon of the pub's lat/longitude
against the vendored SGID county boundaries (utah_counties.geojson, a one-time static download from
the Utah SGID — no live FeatureServer dependency). Only pubs that carry coordinates get a county;
the rest are simply un-countied (the filter shows them under no county).
"""
from __future__ import annotations

import functools
import json
import os

_GEOJSON = os.path.join(os.path.dirname(__file__), "data", "utah_counties.geojson")


@functools.lru_cache(maxsize=1)
def _counties():
    """[(county_name, prepared_polygon)] from the vendored SGID boundaries. Empty if unavailable."""
    try:
        from shapely.geometry import shape
        from shapely.prepared import prep
    except Exception:  # noqa: BLE001 — shapely is a [pubs] dep; degrade to "no county" if absent
        return []
    try:
        fc = json.load(open(_GEOJSON))
    except Exception:  # noqa: BLE001
        return []
    out = []
    for f in fc.get("features", []):
        name = (f.get("properties") or {}).get("county")
        geom = f.get("geometry")
        if name and geom:
            out.append((name, prep(shape(geom))))
    return out


def _f(x) -> float | None:
    try:
        v = float(str(x).strip())
        return v if v == v else None  # reject NaN
    except (TypeError, ValueError):
        return None


def county_of(lat, lon) -> str | None:
    """County name for a lat/lon, or None (no coords / outside Utah / boundaries unavailable)."""
    la, lo = _f(lat), _f(lon)
    if la is None or lo is None or not (36.0 <= la <= 42.5 and -114.5 <= lo <= -108.5):
        return None
    polys = _counties()
    if not polys:
        return None
    from shapely.geometry import Point
    pt = Point(lo, la)
    for name, poly in polys:
        if poly.contains(pt):
            return name
    return None


def county_of_pub(p: dict) -> str | None:
    """County for a pub record (uses its lat/longitude fields)."""
    return county_of(p.get("lat"), p.get("longitude"))
