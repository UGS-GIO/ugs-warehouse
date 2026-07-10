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

from ugs_warehouse.core import config

app = FastAPI(title="ugs-warehouse-review-serving")

_store = GCSStore(bucket=config.BUCKET)

# The internal viewer's static bundle lives under this prefix in the review bucket (cloudbuild's
# build-viewer-review deploys it there with a matching Vite base). index.html backs `/` + SPA routes.
VIEWER_PREFIX = os.environ.get("REVIEW_VIEWER_PREFIX", "review/viewer").strip("/")
VIEWER_INDEX = f"{VIEWER_PREFIX}/index.html"

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
    object_path = object_path.lstrip("/")

    # Root / directory-style paths → the viewer shell.
    if not object_path or object_path.endswith("/") or object_path == VIEWER_PREFIX:
        return _serve_object(VIEWER_INDEX, request)

    try:
        return _serve_object(object_path, request)
    except HTTPException as e:
        # SPA fallback: an unknown path with no file extension is a client-side route → serve the
        # viewer shell. Anything with an extension (a missing asset/data object) stays a real 404.
        if e.status_code == 404 and not _has_extension(object_path):
            return _serve_object(VIEWER_INDEX, request)
        raise


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))


if __name__ == "__main__":
    main()
