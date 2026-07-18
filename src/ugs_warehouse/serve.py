"""IAP review serving app — streams the PRIVATE review bucket read-only.

Deployed as the `ugs-warehouse-review-serving` Cloud Run service (infra/serving.tf) with native Cloud
Run IAP, reached on its *.run.app URL. It serves BOTH the `_review` STAC catalog + assets (GeoParquet,
PMTiles, COG, thumbs) AND the internal viewer SPA — all same-origin, so a signed-in `@utah.gov` user's
single IAP session cookie covers every fetch (viewer shell + catalog + range reads), no CORS.

Read-only by construction: the only GCS verbs used are head + ranged get. The service account
(infra/iam.tf) holds `objectViewer` on the review bucket and nothing else.

HTTP Range is honored (206 + Content-Range) so PMTiles/COG range reads work directly.

Routing: real objects stream from the bucket. The viewer is a client-side-routed SPA, so a not-found
path with NO file extension (an app route like `/map`) falls back to the viewer's index.html; a
not-found path WITH an extension (a missing `.json`/`.pmtiles`) returns a real 404 so the viewer's own
error handling still sees data misses.

Run: `python -m ugs_warehouse.serve` (Cloud Run provides $PORT; WAREHOUSE_BUCKET selects the bucket).
"""
from __future__ import annotations

import os
import re

import obstore as obs
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from obstore.store import GCSStore

from ugs_warehouse import comments, review_catalog
from ugs_warehouse.core import config

app = FastAPI(title="ugs-warehouse-review-serving")

# Cross-origin access for the ugs-map-viewer /hazards-review app (Firebase-token auth, no cookies).
# OFF by default (the IAP service is same-origin); the non-IAP twin sets these. Token auth is
# origin-agnostic (verifies the token's project audience, not the Origin), so it already works on
# Firebase Hosting PREVIEW channels — but CORS must allow their dynamic origins, hence the regex:
#   REVIEW_CORS_ORIGINS      comma-separated fixed origins (prod/dev/localhost)
#   REVIEW_CORS_ORIGIN_REGEX regex for preview channels, e.g. https://ut-dnr-ugs-maps-(prod|dev)--.*\.web\.app
# allow_credentials stays False — auth rides in the Bearer header, not a cookie.
_cors_origins = [o.strip() for o in os.environ.get("REVIEW_CORS_ORIGINS", "").split(",") if o.strip()]
_cors_regex = os.environ.get("REVIEW_CORS_ORIGIN_REGEX", "").strip() or None
if _cors_origins or _cors_regex:
    from fastapi.middleware.cors import CORSMiddleware
    app.add_middleware(
        CORSMiddleware, allow_origins=_cors_origins, allow_origin_regex=_cors_regex, allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"], allow_headers=["Authorization", "Content-Type"],
    )

# Register /api routers BEFORE the catch-all object route below, or they'd be swallowed by /{path}.
app.include_router(comments.router)
app.include_router(comments.status_router)
app.include_router(comments.notif_router)
app.include_router(comments.reviewers_router)
app.include_router(review_catalog.router)


@app.on_event("startup")
async def _init_comments() -> None:
    """Best-effort: create review.comments if the DB is wired. If not, the app still serves files and
    the comment routes 503 until CLOUDSQL_INSTANCE/DB_PASS are set."""
    try:
        await comments.init_schema()
    except Exception as e:  # noqa: BLE001
        print(f"[serve] comments DB not ready ({e}); comment routes will 503 until configured")

# API-only mode for the non-IAP twin service: serve ONLY the /api/* review routes, NOT the private
# review bucket. Without this, a publicly-reachable (Firebase-token-auth) service would stream the
# private review STAC/assets to anyone — the bucket catch-all has no per-request auth of its own.
API_ONLY = os.environ.get("REVIEW_API_ONLY", "").lower() in ("1", "true", "yes")

_store = GCSStore(bucket=config.BUCKET)

# The internal viewer's static bundle lives under this prefix in the review bucket (cloudbuild's
# build-viewer-review deploys it there with a matching Vite base). index.html backs `/` + SPA routes.
VIEWER_PREFIX = os.environ.get("REVIEW_VIEWER_PREFIX", "review/viewer").strip("/")
VIEWER_INDEX = f"{VIEWER_PREFIX}/index.html"

# A second SPA — the hazards-review app (ugs-map-viewer build) — lives under this prefix in the same
# review bucket, served behind the same IAP. Each SPA needs its own index for client-side-route fallback,
# so an unknown route under /review/app/ serves the app shell, not the internal viewer's.
APP_PREFIX = os.environ.get("REVIEW_APP_PREFIX", "review/app").strip("/")
# Per-PR previews of the review app live under <APP_PREFIX>/pr-<n>/ (CI uploads a full build there on
# each PR). Each preview is its own SPA and must fall back to ITS OWN index.html, not the live app's.
_PR_PREVIEW_RE = re.compile(rf"^({re.escape(APP_PREFIX)}/pr-[A-Za-z0-9._-]+)(?:/|$)")
# (prefix, index) longest-prefix-first so a nested prefix wins over a shorter one.
_SPA_INDEXES = sorted(
    [(APP_PREFIX, f"{APP_PREFIX}/index.html"), (VIEWER_PREFIX, VIEWER_INDEX)],
    key=lambda kv: len(kv[0]),
    reverse=True,
)
_SPA_PREFIXES = {APP_PREFIX, VIEWER_PREFIX}


def _spa_index_for(path: str) -> str:
    """The SPA index.html for a client-side route path — the app shell whose prefix owns it. A per-PR
    preview subtree (<APP_PREFIX>/pr-<n>/…) serves its own shell; otherwise the live app or the internal
    viewer (the default for root/unprefixed paths)."""
    m = _PR_PREVIEW_RE.match(path)
    if m:
        return f"{m.group(1)}/index.html"
    for prefix, index in _SPA_INDEXES:
        if path == prefix or path.startswith(prefix + "/"):
            return index
    return VIEWER_INDEX

# Content types by extension. STAC/asset types + the web-asset types a built Vite bundle serves
# (without the latter, index.html falls to octet-stream and the browser downloads it).
_MIME = {
    ".json": "application/json",
    ".geojson": "application/geo+json",
    ".parquet": config.PARQUET_MIME,
    ".pmtiles": config.PMTILES_MIME,
    ".tif": config.COG_MIME,
    ".tiff": config.COG_MIME,
    ".png": "image/png",
    ".xml": "application/xml",
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript",
    ".mjs": "application/javascript",
    ".css": "text/css",
    ".map": "application/json",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".webmanifest": "application/manifest+json",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
}

_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")


def _content_type(path: str) -> str:
    _, dot, ext = path.rpartition(".")
    return _MIME.get(f".{ext}" if dot else "", "application/octet-stream")


def _has_extension(path: str) -> bool:
    """A file has an extension if its last segment contains a dot — asset/data vs. an app route."""
    return "." in path.rsplit("/", 1)[-1]


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/whoami")
def whoami(request: Request) -> dict[str, str]:
    """The IAP-authenticated user, for the viewer's logged-in badge. IAP injects
    `X-Goog-Authenticated-User-Email` as `accounts.google.com:user@domain` on every request that
    passes it. 404 when absent (e.g. hit outside IAP) so the badge simply hides. Display-only — not
    used for authorization (IAP already gated the request)."""
    raw = request.headers.get("x-goog-authenticated-user-email", "")
    email = raw.split(":", 1)[-1] if raw else ""
    if not email:
        raise HTTPException(status_code=404, detail="no IAP identity")
    return {"email": email, "user": email.split("@")[0]}


def _serve_object(object_path: str, request: Request) -> Response:
    """Stream a single bucket object (with Range support). Raises 404 if it doesn't exist."""
    try:
        meta = obs.head(_store, object_path)  # ObjectMeta is a TypedDict
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="not found") from None
    size = meta["size"]
    ctype = _content_type(object_path)
    headers = {"Accept-Ranges": "bytes", "Content-Type": ctype}

    range_header = request.headers.get("range")
    if range_header:
        m = _RANGE_RE.fullmatch(range_header.strip())
        if not m or (not m.group(1) and not m.group(2)):
            raise HTTPException(status_code=416, detail="invalid range")
        start = int(m.group(1)) if m.group(1) else max(0, size - int(m.group(2)))
        end = int(m.group(2)) if (m.group(1) and m.group(2)) else size - 1
        if start > end or start >= size:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        end = min(end, size - 1)
        body = bytes(obs.get_range(_store, object_path, start=start, end=end + 1))
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        headers["Content-Length"] = str(end - start + 1)
        return Response(content=body, status_code=206, headers=headers)

    # Full object — stream (BytesStream is async) so large PMTiles/parquet don't buffer in memory.
    resp = obs.get(_store, object_path)
    headers["Content-Length"] = str(size)
    return StreamingResponse(resp.stream(), status_code=200, headers=headers, media_type=ctype)


@app.get("/{object_path:path}")
def serve(object_path: str, request: Request) -> Response:
    # The non-IAP twin (public, Firebase-token-auth) must NOT stream the private review bucket — it
    # exists only for the /api/* review routes. Everything else 404s there.
    if API_ONLY:
        raise HTTPException(status_code=404, detail="not found")

    object_path = object_path.lstrip("/")

    # Root / directory-style paths → the matching SPA shell (internal viewer or the hazards-review app).
    if not object_path or object_path.endswith("/") or object_path in _SPA_PREFIXES:
        return _serve_object(_spa_index_for(object_path.rstrip("/")), request)

    try:
        return _serve_object(object_path, request)
    except HTTPException as e:
        # SPA fallback: an unknown path with no file extension is a client-side route → serve the shell
        # of whichever app owns the prefix. Anything with an extension (a missing asset) stays a 404.
        if e.status_code == 404 and not _has_extension(object_path):
            return _serve_object(_spa_index_for(object_path), request)
        raise


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))


if __name__ == "__main__":
    main()
