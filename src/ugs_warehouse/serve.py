"""IAP review serving app — streams the PRIVATE review bucket read-only.

Deployed as the `ugs-warehouse-review-serving` Cloud Run service (infra/serving.tf), reachable
ONLY through the IAP-gated load balancer. It serves the `_review` STAC catalog + its assets
(GeoParquet, PMTiles, COG, thumbs) same-origin, so an authenticated browser's single IAP cookie
covers every fetch — no per-asset signed URLs.

Read-only by construction: the only GCS verbs used are head + ranged get. The service account
(infra/iam.tf) holds `objectViewer` on the review bucket and nothing else, so this app cannot
write or delete even if asked to.

HTTP Range is honored (206 + Content-Range) so PMTiles/COG range reads work directly.

Run: `python -m ugs_warehouse.serve` (Cloud Run provides $PORT; WAREHOUSE_BUCKET selects the
bucket — set to the private review bucket in the deploy).
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

# Content types by extension — keep in sync with core.config canonical MIMEs.
_MIME = {
    ".json": "application/json",
    ".geojson": "application/geo+json",
    ".parquet": config.PARQUET_MIME,
    ".pmtiles": config.PMTILES_MIME,
    ".tif": config.COG_MIME,
    ".tiff": config.COG_MIME,
    ".png": "image/png",
    ".xml": "application/xml",
}

_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")


def _content_type(path: str) -> str:
    _, dot, ext = path.rpartition(".")
    return _MIME.get(f".{ext}" if dot else "", "application/octet-stream")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/{object_path:path}")
def serve(object_path: str, request: Request) -> Response:
    object_path = object_path.lstrip("/")
    if not object_path or object_path.endswith("/"):
        raise HTTPException(status_code=404, detail="not found")

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


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))


if __name__ == "__main__":
    main()
