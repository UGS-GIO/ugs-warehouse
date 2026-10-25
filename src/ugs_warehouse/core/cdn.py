"""Invalidate a layer's ArcGIS service paths on the Cloud CDN after a job changes the layer.

A job marks each changed item id pending (one small object per id), refreshes the catalog, then
flushes: it waits for the servers to reload, invalidates every pending id, and drops a marker only
once its invalidation succeeded. A run that dies, or a call that fails, leaves the ids for the next
run's flush. Unset `CDN_URL_MAP` or `CDN_PROJECT` does nothing (local dev, tests, review).
"""
from __future__ import annotations

import logging
import os
import time
import uuid

from . import config, gcs

log = logging.getLogger("ugs-warehouse.cdn")

URL_MAP = os.environ.get("CDN_URL_MAP", "")
PROJECT = os.environ.get("CDN_PROJECT", "")
HOST = os.environ.get("CDN_HOST", "")
SERVICES_PATH = os.environ.get("CDN_SERVICES_PATH", "/ugs/rest/services")
PENDING_PREFIX = os.environ.get("CDN_PENDING_PREFIX", "_cdn/pending")
# The servers keep the catalog and open files in memory for their CACHE_TTL (300s). An invalidation
# sent before they reload refills the CDN with the old layer.
DELAY_S = int(os.environ.get("CDN_INVALIDATE_DELAY_S", "360"))
# The FeatureServer's layer list. An exact path: it covers every query string, nothing below it.
ROOT_PATH = "/"
_API = "https://compute.googleapis.com/compute/v1/projects/{}/global/urlMaps/{}/invalidateCache"


def enabled() -> bool:
    return bool(URL_MAP and PROJECT) and not config.IS_REVIEW_CATALOG


def feature_path(item_id: str) -> str:
    """Every render, layer and query of the item's FeatureServers."""
    return f"{SERVICES_PATH}/{item_id}/*"


def feature_fields(item: dict | None) -> dict | None:
    """The parts of a STAC item the FeatureServer serves (its catalog's parseSTAC)."""
    if item is None:
        return None
    props = item.get("properties") or {}
    renders = props.get("ugs:renders") or {}
    assets = item.get("assets") or {}
    return {
        "title": props.get("title"), "description": props.get("description"),
        "renders": {k: (r.get("title"), r.get("esri_url")) for k, r in renders.items()},
        "assets": {k: (a.get("href"), a.get("ugs:simplify_tolerance")) for k, a in assets.items()
                   if k == "data" or k.startswith("overview")},
    }


def mark(item_ids: list[str]) -> None:
    """Record `item_ids` as pending, before the catalog refresh that publishes them."""
    if not enabled():
        return
    for item_id in item_ids:
        gcs.put_bytes(uuid.uuid4().hex.encode(), f"{PENDING_PREFIX}/{item_id}",
                      content_type="text/plain")


def flush() -> list[str]:
    """Invalidate every pending id after DELAY_S, then drop the markers. Call only after a catalog
    refresh succeeded. Raises, keeping every marker, if any invalidation failed."""
    if not enabled():
        return []
    pending = {p: gcs.get_bytes(p) for p in gcs.list_paths(f"{PENDING_PREFIX}/")}
    if not pending:
        return []
    ids = sorted(p.rsplit("/", 1)[-1] for p in pending)
    time.sleep(DELAY_S)
    invalidate([ROOT_PATH, *map(feature_path, ids)])
    for path, token in pending.items():
        try:
            if gcs.get_bytes(path) == token:  # not re-marked by a run that wrote after our read
                gcs.delete(path)
        except FileNotFoundError:
            pass
    return ids


def _session():
    import google.auth
    from google.auth.transport.requests import AuthorizedSession
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    session = AuthorizedSession(creds)
    # A repeated invalidation is harmless, so POST retries too. About a minute of backoff in all.
    retry = Retry(total=5, backoff_factor=2, status_forcelist=(429, 500, 502, 503, 504),
                  allowed_methods=None, raise_on_status=False)
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def invalidate(paths: list[str]) -> None:
    """One invalidateCache call per path, since a rule holds one path. Tries every path, then
    raises if any failed. Returns once the CDN accepts each call, not when the purge completes."""
    session = _session()
    url = _API.format(PROJECT, URL_MAP)
    failed = []
    for path in paths:
        rule = {"path": path, **({"host": HOST} if HOST else {})}
        try:
            session.post(url, json=rule, timeout=30).raise_for_status()
            log.info("CDN invalidated %s%s", HOST, path)
        except Exception:
            log.exception("CDN invalidation of %s%s FAILED", HOST, path)
            failed.append(path)
    if failed:
        raise RuntimeError(f"CDN invalidation failed for {failed}")
