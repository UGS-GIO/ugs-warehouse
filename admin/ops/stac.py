"""Read the published STAC catalog for coverage stats — no auth, no DB. The catalog is public on
the CDN, so this works from anywhere (incl. local dev)."""
from __future__ import annotations

import concurrent.futures as cf
import json
import time
import urllib.error
import urllib.request

from django.conf import settings

PUB_COLLECTIONS = ["ugs-publications", "ugs-mining-district-files", "ugs-external"]


def _get(url: str):
    try:
        with urllib.request.urlopen(url, timeout=20) as r:  # noqa: S310 (https CDN)
            return json.load(r)
    except Exception:
        return None


# The catalog changes slowly, but the dashboard reads it from several panels/tabs. Memoize the
# whole-catalog scans for a short TTL so a page load (health panel + Data tab) does ONE scan, not N.
_CACHE: dict = {}
_CACHE_TTL = 60.0


def _cached(key: str, fn):
    now = time.monotonic()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]
    val = fn()
    _CACHE[key] = (now, val)
    return val


def service_health() -> list[dict]:
    """Ping the public serving surfaces (STAC catalog, viewer, + any configured) → up/down lights.
    Concurrent, short timeouts. This is the *serving* side of observability, not the jobs."""
    checks = settings.HEALTH_CHECKS

    def ping(c: dict) -> dict:
        try:
            req = urllib.request.Request(c["url"], method="GET")  # noqa: S310 (https)
            with urllib.request.urlopen(req, timeout=8) as r:  # noqa: S310
                code = r.status
            return {**c, "ok": 200 <= code < 400, "detail": str(code)}
        except urllib.error.HTTPError as e:  # reachable but non-2xx
            return {**c, "ok": False, "detail": f"HTTP {e.code}"}
        except Exception as e:  # noqa: BLE001
            return {**c, "ok": False, "detail": f"{type(e).__name__}"}

    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        return list(ex.map(ping, checks))


def _has_cog(item: dict) -> bool:
    for a in (item.get("assets") or {}).values():
        t, h, roles = a.get("type") or "", a.get("href") or "", a.get("roles") or []
        if "profile=cloud-optimized" in t or "cloud-optimized" in roles or h.endswith(".cog.tif"):
            return True
    return False


def cog_coverage() -> dict:
    """Per publication-collection COG coverage. Cached (TTL) — scanned by both panels on a page."""
    return _cached("cog_coverage", _cog_coverage)


def _cog_coverage() -> dict:
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
    """Vector serving topics + whether each has a bound style render. Cached (TTL)."""
    return _cached("serving_topics", _serving_topics)


def _serving_topics() -> list[dict]:
    idx = _get(f"{settings.STAC_BASE}/ugs-serving-topics/items.json") or {}
    rows = []
    for it in idx.get("items", []):
        props = it.get("properties") or {}
        schema, layer = props.get("ugs:dbt_schema"), props.get("ugs:layer")
        rows.append({"id": it["id"], "styled": bool(props.get("renders")),
                     "rows": props.get("ugs:row_count"),
                     # The retire CLI addresses the serving TABLE, not the item id.
                     "fqn": f"{schema}.{layer}" if schema and layer else ""})
    rows.sort(key=lambda r: r["id"])
    return rows


# The registry's inputs are slow (pub-metadata CSV/DB read + a full GCS listing of the COG bucket),
# but change slowly — so cache them briefly. Without this, every filter keystroke (300ms debounce)
# would re-list the whole bucket. TTL keeps "harvested" near-live as a run progresses.
_INPUTS_CACHE: dict = {"ts": -1e9, "pubs": None, "att_zips": None, "cogs": None}
_INPUTS_TTL = 60.0  # seconds
COG_PREFIX = "geolmap/cogs"


def _harvest_inputs() -> tuple[list[dict], dict[str, list[str]], set[str]]:
    """Cached (pubs, series_id→zip-urls, harvested-COG basenames). Raises on read failure so the
    caller can show *why* the registry is empty instead of a silent blank."""
    import time

    from ugs_warehouse.core import gcs
    from ugs_warehouse.pubs import source

    now = time.monotonic()
    if _INPUTS_CACHE["pubs"] is None or now - _INPUTS_CACHE["ts"] > _INPUTS_TTL:
        pubs = source.read_pubs()
        att_zips: dict[str, list[str]] = {}
        for a in source.read_attachments():
            sid = (a.get("series_id") or "").strip().upper()
            url = (a.get("pub_url") or "").strip()
            if url.lower().endswith(".zip"):
                if not url.startswith(("http://", "https://")):
                    url = f"https://ugspub.nr.utah.gov/publications/{url}"
                att_zips.setdefault(sid, []).append(url)
        # Bucket truth: a pub is harvested when geolmap/cogs/{series_id}.cog.tif exists (the same
        # object the harvester's skip-existing check writes). This is the live harvest count.
        cogs = {p.split("/")[-1].removesuffix(".cog.tif").upper()
                for p in gcs.list_paths(COG_PREFIX) if p.endswith(".cog.tif")}
        _INPUTS_CACHE.update(ts=now, pubs=pubs, att_zips=att_zips, cogs=cogs)
    return _INPUTS_CACHE["pubs"], _INPUTS_CACHE["att_zips"], _INPUTS_CACHE["cogs"]


def harvested_ids() -> set[str]:
    """Upper-cased series ids that have a COG in the bucket — i.e. successfully harvested."""
    _, _, cogs = _harvest_inputs()
    return cogs


def harvested_bucket_count() -> dict:
    """Live count of COGs actually in the bucket (geolmap/cogs/*.cog.tif) — the harvest truth, ahead
    of STAC (which only updates after pubs-ingest binds them). {ok, count} or {ok: False, message}."""
    try:
        _, _, cogs = _harvest_inputs()
        return {"ok": True, "count": len(cogs)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"{type(e).__name__}: {e}"}


def harvest_series_codes() -> list[str]:
    """Distinct series codes (OFR, M, …) for the filter dropdown, from cached pubs."""
    from ugs_warehouse.pubs.sink_stac import series_code
    pubs, _, _ = _harvest_inputs()
    return sorted({c for p in pubs if (c := series_code(p.get("series_id") or ""))})


def get_harvest_status(search_query: str = "", status_filter: str = "", series_filter: str = "") -> list[dict]:
    """Per-publication harvest status (harvested/pending/pdf_only/placeholder), filtered. The
    'harvested' signal is the COG bucket (live), not STAC (which only updates after pubs-ingest)."""
    from ugs_warehouse.pubs.sink_stac import series_code

    pubs, att_zips, existing_cogs = _harvest_inputs()
    series_filter = (series_filter or "").strip().upper()
    status_filter = (status_filter or "").strip().lower()
    search_query = (search_query or "").strip().upper()

    rows = []
    for p in pubs:
        sid = (p.get("series_id") or "").strip()
        if not sid:
            continue
        sid_upper = sid.upper()
        if series_filter and series_code(sid_upper) != series_filter:
            continue
        title = p.get("pub_name") or ""
        if search_query and search_query not in sid_upper and search_query not in title.upper():
            continue

        zurls = att_zips.get(sid_upper, [])
        if "XXXX" in sid_upper:
            status = "placeholder"
        elif sid_upper in existing_cogs:
            status = "harvested"
        elif not zurls:
            status = "pdf_only"
        else:
            status = "pending"
        if status_filter and status != status_filter:
            continue

        rows.append({
            "id": sid,
            "title": title,
            "scale": p.get("pub_scale") or "",
            "status": status,
            "zips": [{"url": u, "name": u.split("/")[-1]} for u in zurls],
            "year": p.get("pub_year") or "",
            "series_code": series_code(sid_upper),  # viewer collection id (OFR-593 → OFR)
        })

    rows.sort(key=lambda r: r["id"])
    return rows
