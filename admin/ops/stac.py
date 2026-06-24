"""Read the published STAC catalog for coverage stats — no auth, no DB. The catalog is public on
the CDN, so this works from anywhere (incl. local dev)."""
from __future__ import annotations

import concurrent.futures as cf
import json
import urllib.request

from django.conf import settings

PUB_COLLECTIONS = ["ugs-publications", "ugs-mining-district-files", "ugs-external"]


def _get(url: str):
    try:
        with urllib.request.urlopen(url, timeout=20) as r:  # noqa: S310 (https CDN)
            return json.load(r)
    except Exception:
        return None


def _has_cog(item: dict) -> bool:
    for a in (item.get("assets") or {}).values():
        t, h, roles = a.get("type") or "", a.get("href") or "", a.get("roles") or []
        if "profile=cloud-optimized" in t or "cloud-optimized" in roles or h.endswith(".cog.tif"):
            return True
    return False


def cog_coverage() -> dict:
    """Per publication-collection: total items + how many carry a COG, with the COG item ids."""
    base = settings.STAC_BASE
    out = {"collections": [], "total_items": 0, "total_cogs": 0}
    for coll in PUB_COLLECTIONS:
        cat = _get(f"{base}/{coll}/catalog.json")
        if not cat:
            out["collections"].append({"id": coll, "items": 0, "cogs": 0, "cog_ids": [], "error": True})
            continue
        series = [l["href"].split("/")[-2] for l in cat.get("links", []) if l.get("rel") == "child"]

        def scan(s):
            idx = _get(f"{base}/{coll}/{s}/items.json") or {}
            items = idx.get("items", [])
            return len(items), [it["id"] for it in items if _has_cog(it)]

        total, cog_ids = 0, []
        with cf.ThreadPoolExecutor(max_workers=16) as ex:
            for n, ids in ex.map(scan, series):
                total += n
                cog_ids += ids
        cog_ids.sort()
        out["collections"].append({"id": coll, "items": total, "cogs": len(cog_ids), "cog_ids": cog_ids})
        out["total_items"] += total
        out["total_cogs"] += len(cog_ids)
    return out


def serving_topics() -> list[dict]:
    """Vector serving topics + whether each has a bound style render (quick catalog glance)."""
    idx = _get(f"{settings.STAC_BASE}/ugs-serving-topics/items.json") or {}
    rows = []
    for it in idx.get("items", []):
        props = it.get("properties") or {}
        rows.append({"id": it["id"], "styled": bool(props.get("renders")),
                     "rows": props.get("ugs:row_count")})
    rows.sort(key=lambda r: r["id"])
    return rows
