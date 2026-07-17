"""Review STAC catalog API — lists the review catalog's items with SHORT-LIVED SIGNED URLs for their
otherwise-private assets, so the public hazards-review app (ugs-map-viewer) can RENDER + comment on
review data without the private review bucket being public.

Why signed URLs (not a proxy / not IAP): maplibre's PMTiles protocol and duckdb-wasm's GeoParquet range
reads fetch cross-origin and can't cleanly attach an auth header. A signed URL is a *plain* URL, so both
"just work" while the bucket stays private. This is the standard GCS pattern for browser access to
private object storage with Range reads. Signing uses the runtime SA's own credentials via obstore
(IAM signBlob on Cloud Run metadata creds — no key stored; the SA needs `iam.serviceAccounts.signBlob`).

Auth: the same reviewer identity as the comments API (`_author` — IAP header or Firebase/Entra bearer),
PLUS an allow-list gate. `_author` only proves *a* valid identity, not review-group membership (Cloud
Identity groups are org-blocked), and signed URLs hand out private pre-publication data — so restrict to
trusted domains/emails before minting.
"""
from __future__ import annotations

import json
import os
import posixpath
from datetime import timedelta

import obstore as obs
from fastapi import APIRouter, HTTPException, Request
from obstore.store import GCSStore

from ugs_warehouse.comments import _author
from ugs_warehouse.core import config

router = APIRouter(prefix="/api/review-catalog", tags=["review-catalog"])

# Signed-URL lifetime — short enough to bound a leaked URL, long enough to cover a browsing session and
# its PMTiles/GeoParquet range reads. The frontend re-fetches the catalog to refresh before expiry.
_TTL_MIN = int(os.environ.get("REVIEW_SIGNED_URL_TTL_MIN", "60"))
_TTL = timedelta(minutes=_TTL_MIN)

# Allow-list gate (see module docstring). Default: any @utah.gov identity. Tighten with an explicit
# email list when the review group is known. Domains OR emails — either match authorizes.
_ALLOWED_DOMAINS = {d.strip().lower() for d in os.environ.get("REVIEW_ALLOWED_DOMAINS", "utah.gov").split(",") if d.strip()}
_ALLOWED_EMAILS = {e.strip().lower() for e in os.environ.get("REVIEW_ALLOWED_EMAILS", "").split(",") if e.strip()}

# Asset keys worth surfacing to the map viewer, in preference order for the "primary" data asset.
# Values in the review bucket are private → signed; anything off our base (CDN styles) is left as-is.
_ASSET_KEYS = ("pmtiles", "geoparquet", "data", "cog", "thumbnail")

# Bound the catalog crawl so a malformed/cyclic catalog can't fan out unboundedly.
_MAX_DOCS = int(os.environ.get("REVIEW_CATALOG_MAX_DOCS", "1000"))

_store = GCSStore(bucket=config.BUCKET)


def _domain_ok(domain: str) -> bool:
    """True if `domain` equals or is a subdomain of an allowed domain (dot-boundary match, so
    `dnr.utah.gov` passes for `utah.gov` but `notutah.gov` does not)."""
    return any(domain == d or domain.endswith("." + d) for d in _ALLOWED_DOMAINS)


def _require_reviewer(request: Request) -> str:
    """The caller's email if they pass auth AND the allow-list; 401/403 otherwise."""
    email = _author(request)  # 401 if no IAP identity / valid bearer
    lower = email.lower()
    domain = lower.rsplit("@", 1)[-1] if "@" in lower else ""
    if lower in _ALLOWED_EMAILS or _domain_ok(domain):
        return email
    raise HTTPException(status_code=403, detail="not authorized for review data")


def _read_json(object_path: str) -> dict | None:
    """A JSON object from the review bucket, or None if absent/unparseable."""
    try:
        raw = bytes(obs.get(_store, object_path).bytes())
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
    """{asset_key: bucket_object_path} for the private assets we surface on this item."""
    assets = item.get("assets") or {}
    paths: dict[str, str] = {}
    for key in _ASSET_KEYS:
        a = assets.get(key)
        href = a.get("href") if isinstance(a, dict) else None
        if href:
            op = _object_path(href, item_path)
            if op:
                paths[key] = op
    return paths


@router.get("")
def list_review_catalog(request: Request) -> dict:
    """Reviewable items from the review STAC catalog, each with signed URLs for its private assets.

    Response: {ttl_seconds, items: [{id, collection, title, primary_key, bbox, assets:{key: url}}]}.
    Assets are short-lived signed GCS URLs — the caller refetches before `ttl_seconds` elapses.
    """
    _require_reviewer(request)

    collected = _collect_items()

    # Batch-sign every unique asset path in one call (fewer signBlob round-trips), then assemble.
    uniq = sorted({p for item, item_path in collected for p in _asset_paths(item, item_path).values()})
    signed: dict[str, str] = {}
    if uniq:
        urls = obs.sign(_store, "GET", uniq, _TTL)
        signed = dict(zip(uniq, urls if isinstance(urls, list) else [urls]))

    items = []
    for item, item_path in collected:
        props = item.get("properties") or {}
        assets = {key: signed[p] for key, p in _asset_paths(item, item_path).items() if p in signed}
        items.append({
            "id": item.get("id"),
            "collection": item.get("collection"),
            "title": props.get("title") or item.get("id"),
            "primary_key": props.get("ugs:primary_key", "pk"),
            "bbox": item.get("bbox"),
            "assets": assets,
        })

    return {"ttl_seconds": _TTL_MIN * 60, "items": items}
