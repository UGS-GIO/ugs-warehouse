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
        series = [lnk["href"].split("/")[-2] for lnk in cat.get("links", []) if lnk.get("rel") == "child"]

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


def get_harvest_status(search_query: str = "", status_filter: str = "", series_filter: str = "") -> list[dict]:
    """Calculate and filter the harvest status of each publication."""
    from ugs_warehouse.pubs import source
    from ugs_warehouse.core import gcs
    from ugs_warehouse.vector.sink_stac import series_code

    # 1. Load publications & attachments
    try:
        pubs = source.read_pubs()
        attachments = source.read_attachments()
    except Exception:
        return []

    # Map series_id (uppercased) to its list of zip URLs
    att_zips: dict[str, list[str]] = {}
    for a in attachments:
        sid = (a.get("series_id") or "").strip().upper()
        url = (a.get("pub_url") or "").strip()
        if url.lower().endswith(".zip"):
            if not url.startswith(("http://", "https://")):
                url = f"https://ugspub.nr.utah.gov/publications/{url}"
            att_zips.setdefault(sid, []).append(url)

    # 2. Fetch existing COGs list from GCS (under prefix "geolmap/cogs")
    try:
        existing_paths = gcs.list_paths("geolmap/cogs")
    except Exception:
        existing_paths = []

    existing_cogs = set()
    for p in existing_paths:
        if p.endswith(".cog.tif"):
            base = p.split("/")[-1].removesuffix(".cog.tif").upper()
            existing_cogs.add(base)

    series_filter = (series_filter or "").strip().upper()
    status_filter = (status_filter or "").strip().lower()
    search_query = (search_query or "").strip().upper()

    rows = []
    for p in pubs:
        sid = (p.get("series_id") or "").strip()
        if not sid:
            continue
        sid_upper = sid.upper()

        # Series filter
        scode = series_code(sid_upper)
        if series_filter and scode != series_filter:
            continue

        # Search filter (ID or title)
        title = p.get("pub_name") or ""
        if search_query and (search_query not in sid_upper and search_query not in title.upper()):
            continue

        zurls = att_zips.get(sid_upper, [])
        is_spatial = len(zurls) > 0

        # Determine state
        if "XXXX" in sid_upper:
            status = "placeholder"
        elif sid_upper in existing_cogs:
            status = "harvested"
        elif not is_spatial:
            status = "pdf_only"
        else:
            status = "pending"

        # Status filter
        if status_filter and status != status_filter:
            continue

        rows.append({
            "id": sid,
            "title": title,
            "scale": p.get("pub_scale") or "",
            "status": status,
            "zips": [{"url": u, "name": u.split("/")[-1]} for u in zurls],
            "year": p.get("pub_year") or "",
        })

    # Sort alphabetical by publication ID
    rows.sort(key=lambda r: r["id"])
    return rows
