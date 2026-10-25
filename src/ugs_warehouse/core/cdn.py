"""Invalidate a layer's ArcGIS service paths on the Cloud CDN after a job changes the layer.

A job marks each changed item id pending (one small object per id), refreshes the catalog, then
flushes: it marks its own ids again, waits for the servers to reload, invalidates every pending id,
and drops a marker only once its invalidation succeeded. A run that dies, or a call that fails,
leaves the ids for the next run's flush. Unset `CDN_URL_MAP` or `CDN_PROJECT` does nothing (local
dev, tests, review).
"""
from __future__ import annotations

import logging
import os
import re
import threading
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
# The FeatureServer's layer list and services directory. Exact paths: each covers every query
# string and nothing below it. Only a trailing `*` is a wildcard.
LISTING_PATHS = ("/", SERVICES_PATH, f"{SERVICES_PATH}/")
_API = "https://compute.googleapis.com/compute/v1/projects/{}/global/urlMaps/{}/invalidateCache"


def enabled() -> bool:
    return bool(URL_MAP and PROJECT) and not config.IS_REVIEW_CATALOG


_own: set[str] = set()  # the ids this process marked and has not flushed
_own_lock = threading.Lock()


def feature_paths(item_id: str) -> tuple[str, str]:
    """The item's services folder, and every render, layer and query of its FeatureServers."""
    return f"{SERVICES_PATH}/{item_id}", f"{SERVICES_PATH}/{item_id}/*"


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
    with _own_lock:
        _own.update(item_ids)


def _pending() -> dict[str, bytes]:
    """{marker path: token}, skipping a marker another run dropped since the listing."""
    out = {}
    for path in gcs.list_paths(f"{PENDING_PREFIX}/"):
        try:
            out[path] = gcs.get_bytes(path)
        except FileNotFoundError:
            continue
    return out


def _named(item_id: str, problem: str) -> bool:
    return re.search(rf"(?<![\w-]){re.escape(item_id)}(?![\w-])", problem) is not None


def flush(problems: list[str] | None = None) -> list[str]:
    """Invalidate every pending id after DELAY_S, then drop the markers; returns the ids. Call right
    after a catalog refresh, with the problems it reported: an id a problem names stays pending.

    This process's own ids are marked again first: another run's flush may have taken their
    markers before this refresh published them. Raises, keeping every marker, if any invalidation
    failed."""
    if not enabled():
        return []
    with _own_lock:
        own = sorted(_own)
    mark(own)
    pending = _pending()
    ids = sorted({p.rsplit("/", 1)[-1] for p in pending} | set(own))
    held = [i for i in ids if any(_named(i, p) for p in problems or ())]
    if held:
        log.warning("catalog refresh reported problems for %s; they stay pending", held)
    ids = [i for i in ids if i not in held]
    if not ids:
        return []
    time.sleep(DELAY_S)
    invalidate([*LISTING_PATHS, *(p for i in ids for p in feature_paths(i))])
    for path, token in pending.items():
        if path.rsplit("/", 1)[-1] not in ids:
            continue
        # Dropped only if no run marked it again since our read. obstore has no conditional delete,
        # so a mark in the moment between this read and the delete is lost.
        try:
            if gcs.get_bytes(path) == token:
                gcs.delete(path)
        except FileNotFoundError:
            pass
    with _own_lock:
        _own.difference_update(ids)
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
