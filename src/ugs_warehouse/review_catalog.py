"""Review STAC catalog API — lists the review catalog's items with SHORT-LIVED SIGNED URLs for their
otherwise-private assets, so the public hazards-review app (ugs-map-viewer) can RENDER + comment on
review data without the private review bucket being public.

Why signed URLs (not a proxy / not IAP): maplibre's PMTiles protocol and duckdb-wasm's GeoParquet range
reads fetch cross-origin and can't cleanly attach an auth header. A signed URL is a *plain* URL, so both
"just work" while the bucket stays private. This is the standard GCS pattern for browser access to
private object storage with Range reads. Signing uses the runtime SA's own credentials via obstore
(IAM signBlob on Cloud Run metadata creds — no key stored; the SA needs `iam.serviceAccounts.signBlob`).

Auth (READ): any authenticated hazards-review user may VIEW review data — same policy as viewing review
comments. `_author` (IAP header OR Firebase/Entra bearer) is the only gate; there is deliberately NO
group/domain allow-list on reads. Review data is pre-publication, not confidential (ingest#195), and the
product intent is that everyone who can reach the hazards-review route can see it. WRITE authorization
(who may create/edit comments) is separate and lives in `comments._require_editor`.
"""
from __future__ import annotations

import json
import os
import posixpath
from datetime import timedelta

import obstore as obs
from fastapi import APIRouter, Request
from obstore.store import GCSStore

from ugs_warehouse.comments import _author
from ugs_warehouse.core import config, gcs

router = APIRouter(prefix="/api/review-catalog", tags=["review-catalog"])

# Signed-URL lifetime — short enough to bound a leaked URL, long enough to cover a browsing session and
# its PMTiles/GeoParquet range reads. The frontend re-fetches the catalog to refresh before expiry.
_TTL_MIN = int(os.environ.get("REVIEW_SIGNED_URL_TTL_MIN", "60"))
_TTL = timedelta(minutes=_TTL_MIN)

# Bound the catalog crawl so a malformed/cyclic catalog can't fan out unboundedly.
_MAX_DOCS = int(os.environ.get("REVIEW_CATALOG_MAX_DOCS", "1000"))

_store = GCSStore(bucket=config.BUCKET)


def _read_json(object_path: str) -> dict | None:
    """A JSON object from the review bucket, or None if absent/unparseable."""
    try:
        # get_bytes handles the gzipped catalog/collection/items indexes — obstore chokes on GCS's
        # stripped Content-Length, so it falls back to google-cloud-storage — and gunzips; a genuine
        # 404 still raises FileNotFoundError. Raw obstore here 500'd the crawl on the gzipped root. (#341)
        raw = gcs.get_bytes(object_path)
    except FileNotFoundError:
        return None
    try:
        doc = json.loads(raw)
        return doc if isinstance(doc, dict) else None
    except (ValueError, UnicodeDecodeError):
        return None


def _object_path(href: str, parent_path: str) -> str | None:
    """Bucket object path an asset/link href resolves to, or None if it points off-bucket (external
    URL). Handles both absolute hrefs under our base and relative hrefs (resolved against the parent
    doc's directory, as STAC clients do)."""
    base = config.PUBLIC_BASE_URL.rstrip("/") + "/"
    if href.startswith(base):
        return href[len(base):].lstrip("/")
    if href.startswith(("http://", "https://")):
        return None  # some other host (e.g. CDN styles) — not ours to sign
    # Relative href → resolve against the parent object's directory.
    joined = posixpath.normpath(posixpath.join(posixpath.dirname(parent_path), href))
    return joined.lstrip("/")


def _collect_items() -> list[tuple[dict, str]]:
    """Every STAC Item in the review catalog, as (item_doc, its_object_path). Walks catalog/collection
    child links, preferring each collection's compact `items.json` index over per-item reads."""
    root = f"{config.STAC_PREFIX}/catalog.json"
    stack = [root]
    seen: set[str] = set()
    out: list[tuple[dict, str]] = []
    ids: set[str] = set()

    def _add(doc: dict, path: str) -> None:
        iid = doc.get("id")
        if iid and iid not in ids:
            ids.add(iid)
            out.append((doc, path))

    while stack and len(seen) < _MAX_DOCS:
        path = stack.pop()
        if path in seen:
            continue
        seen.add(path)
        doc = _read_json(path)
        if not doc:
            continue
        typ = doc.get("type")

        if typ == "Feature":  # a STAC Item
            _add(doc, path)
            continue

        # Catalog or Collection: prefer the compact items.json index next to a collection.
        used_index = False
        if typ == "Collection":
            idx_path = posixpath.join(posixpath.dirname(path), "items.json")
            idx = _read_json(idx_path)
            if idx and isinstance(idx.get("items"), list):
                used_index = True
                for it in idx["items"]:
                    if isinstance(it, dict):
                        _add(it, idx_path)

        for link in doc.get("links", []):
            if not isinstance(link, dict):
                continue
            rel, href = link.get("rel"), link.get("href")
            if not href:
                continue
            if rel == "child":
                cp = _object_path(href, path)
                if cp:
                    stack.append(cp)
            elif rel == "item" and not used_index:
                ip = _object_path(href, path)
                if ip:
                    it = _read_json(ip)
                    if it:
                        _add(it, ip)

    return out


def _asset_paths(item: dict, item_path: str) -> dict[str, str]:
    """{asset_key: bucket_object_path} for EVERY asset whose href resolves to a review-bucket object.
    Signs all private data assets (pmtiles/geoparquet/thumbnail/cog/related-tables/…); assets whose href
    points off our base (e.g. CDN style_url, sprites) resolve to None and are left untouched."""
    assets = item.get("assets") or {}
    paths: dict[str, str] = {}
    for key, a in assets.items():
        href = a.get("href") if isinstance(a, dict) else None
        if href:
            op = _object_path(href, item_path)
            if op:
                paths[key] = op
    return paths


def _sign_item_assets(item: dict, item_path: str, signed: dict[str, str]) -> dict:
    """A shallow copy of the STAC item with each private asset's `href` swapped for its signed URL.
    The full item is preserved otherwise, so the map viewer's existing STAC→PMTiles resolver
    (`resolveStacPMTilesLayer`) consumes it unchanged."""
    ap = _asset_paths(item, item_path)
    if not ap:
        return item
    assets = dict(item.get("assets") or {})
    for key, objpath in ap.items():
        if objpath in signed and isinstance(assets.get(key), dict):
            assets[key] = {**assets[key], "href": signed[objpath]}
    return {**item, "assets": assets}


@router.get("")
def list_review_catalog(request: Request) -> dict:
    """The review STAC catalog's items, returned VERBATIM except that every private-bucket asset href is
    replaced with a short-lived signed GCS URL. The map viewer feeds these straight into its existing
    STAC layer pipeline. The caller refetches before `ttl_seconds` elapses to refresh the URLs.
    Any authenticated user may read (no allow-list) — viewing review data is open to all app users.
    """
    _author(request)  # 401 if unauthenticated; write authz (comments) is gated separately

    collected = _collect_items()

    # Batch-sign every unique private asset path in one call (fewer signBlob round-trips).
    uniq = sorted({p for item, item_path in collected for p in _asset_paths(item, item_path).values()})
    signed: dict[str, str] = {}
    if uniq:
        urls = obs.sign(_store, "GET", uniq, _TTL)
        signed = dict(zip(uniq, urls if isinstance(urls, list) else [urls]))

    items = [_sign_item_assets(item, item_path, signed) for item, item_path in collected]
    return {"ttl_seconds": _TTL_MIN * 60, "items": items}
