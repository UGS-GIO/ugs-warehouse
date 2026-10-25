"""Invalidate a layer's ArcGIS service paths on the Cloud CDN after an ingest changes it.

The FeatureServer sits behind the maps-assets load balancer, whose CDN keeps each response for its
`s-maxage`. Unset `CDN_URL_MAP` or `CDN_PROJECT` invalidates nothing (local dev, tests, review).
"""
from __future__ import annotations

import logging
import os
import time

from . import config

log = logging.getLogger("ugs-warehouse.cdn")

URL_MAP = os.environ.get("CDN_URL_MAP", "")
PROJECT = os.environ.get("CDN_PROJECT", "")
HOST = os.environ.get("CDN_HOST", "")
SERVICES_PATH = os.environ.get("CDN_SERVICES_PATH", "/ugs/rest/services")
# The FeatureServer keeps the catalog and open files in memory for its CACHE_TTL. An invalidation
# sent before it reloads them would refill the CDN with the old layer.
DELAY_S = int(os.environ.get("CDN_INVALIDATE_DELAY_S", "0"))
_API = "https://compute.googleapis.com/compute/v1/projects/{}/global/urlMaps/{}/invalidateCache"


def enabled() -> bool:
    return bool(URL_MAP and PROJECT) and not config.IS_REVIEW_CATALOG


def feature_path(item_id: str) -> str:
    """Every render, layer and query of the item's FeatureServers."""
    return f"{SERVICES_PATH}/{item_id}/*"


def invalidate(paths: list[str]) -> None:
    """One invalidateCache call per path, since a rule holds one path. Tries every path, then
    raises if any failed. Returns once the CDN accepts each call, not when the purge completes."""
    import google.auth
    from google.auth.transport.requests import AuthorizedSession

    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    session = AuthorizedSession(creds)
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


def invalidate_features(item_ids: list[str]) -> None:
    """Invalidate the FeatureServer paths of `item_ids`, after DELAY_S."""
    if not item_ids or not enabled():
        return
    time.sleep(DELAY_S)
    invalidate([feature_path(i) for i in item_ids])
