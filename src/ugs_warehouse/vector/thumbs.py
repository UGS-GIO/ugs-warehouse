"""Styled-preview thumbnails for vector serving-topics — a PNG of the PMTiles layer rendered with its
bound MapLibre style (or a neutral "sand" style when unstyled), so the catalog shows a real preview.

Pubs get cover thumbnails from a PDF page (`pubs/thumbs.py`); topics are vector + style, so they can't
be rasterised the same way. MapLibre needs WebGL, so we render in headless Chromium — but driven from
Python via Playwright (no separate Node project): the browser runs the SAME maplibre-gl + pmtiles libs
the viewer uses (vendored in `render_assets/`), so thumbnails are WYSIWYG. We compose the full style
exactly like the viewer (viewer/src/Map.tsx): the ugs-styles JSON is layers-ONLY, so we wrap it into a
version-8 style with a vector source over `pmtiles://`, a sand background, and `source`/`source-layer`
injected per layer.

Content-addressed + idempotent: each PNG carries a `.sha` sidecar of `RENDERER_VERSION + style bytes`.
A run skips a topic whose sidecar matches → a NEW layer renders (no PNG yet), a SYMBOLOGY change
re-renders (style bytes change), an unchanged topic is skipped. So it's safe to chain after every
vector ingest AND every restyle; it only does work when something changed. Sharded via
CLOUD_RUN_TASK_INDEX/COUNT, same as the pubs jobs.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import urllib.request

from ..core import config, gcs
from ..pubs.harvest import _series_ctx, hlog, outcome_category

COLLECTION = "ugs-serving-topics"
# Bump to force a global re-render (renderer/style-composition change) without touching style bytes.
RENDERER_VERSION = "1"
RENDER_TIMEOUT_MS = int(os.environ.get("TOPIC_THUMB_TIMEOUT_MS", "30000"))
THUMB_W = int(os.environ.get("TOPIC_THUMB_W", "480"))
THUMB_H = int(os.environ.get("TOPIC_THUMB_H", "320"))

_ASSETS = os.path.join(os.path.dirname(__file__), "render_assets")

# Neutral fallback cartography (sand) — used when a topic has no bound style, or its style is purely
# symbol/label layers (dropped below). Covers fill / line / circle so any geometry type shows.
SAND_LAYERS = [
    {"id": "d-fill", "type": "fill", "paint": {"fill-color": "#d8c39a", "fill-opacity": 0.55, "fill-outline-color": "#7a5c2e"}},
    {"id": "d-line", "type": "line", "paint": {"line-color": "#7a5c2e", "line-width": 1.1}},
    {"id": "d-circle", "type": "circle", "paint": {"circle-color": "#9c6b30", "circle-radius": 3.2,
                                                   "circle-stroke-width": 0.5, "circle-stroke-color": "#4a2f12", "circle-opacity": 0.9}},
]


def thumb_object(stem: str) -> str:
    return f"{config.THUMBS_PREFIX}/{stem}/{stem}.png"


def sha_object(stem: str) -> str:
    return f"{config.THUMBS_PREFIX}/{stem}/{stem}.sha"


def _fetch(url: str, timeout: int = 30) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310 — fixed CDN host
        return r.read()


def _asset(name: str) -> str:
    with open(os.path.join(_ASSETS, name), encoding="utf-8") as f:
        return f.read()


def _style_url(item: dict) -> str | None:
    """The default render's style URL (ugs:renders.default.style_url), or None if the topic is unstyled."""
    renders = (item.get("properties") or {}).get("ugs:renders") or {}
    return (renders.get("default") or {}).get("style_url")


def _source_layer(item: dict) -> str:
    """The PMTiles source-layer name — from the web-map `pmtiles` link's `pmtiles:layers`, else the id."""
    for link in item.get("links") or []:
        if link.get("rel") == "pmtiles" and (link.get("pmtiles:layers") or []):
            return link["pmtiles:layers"][0]
    return item["id"]


def _style_hash(style_url: str | None) -> tuple[str, bytes | None]:
    """(content hash, fetched style bytes). Unstyled — or a style URL that won't fetch — hashes the
    'sand' sentinel, matching what the renderer actually draws."""
    h = hashlib.sha256()
    h.update(RENDERER_VERSION.encode())
    style_bytes = None
    if style_url:
        try:
            style_bytes = _fetch(style_url)
        except Exception:  # noqa: BLE001 — unreachable style → sand
            style_bytes = None
    h.update(style_bytes if style_bytes is not None else b"sand")
    return h.hexdigest(), style_bytes


def _layers_from(style_bytes: bytes | None) -> list[dict]:
    """Style layers minus symbol/label layers (need glyphs/sprites we don't carry → drop for thumbnails).
    Empty/unparseable → the sand fallback."""
    if not style_bytes:
        return SAND_LAYERS
    try:
        layers = [lyr for lyr in (json.loads(style_bytes).get("layers") or []) if lyr.get("type") != "symbol"]
        return layers or SAND_LAYERS
    except Exception:  # noqa: BLE001
        return SAND_LAYERS


def _compose_style(layers: list[dict], pmtiles_url: str, source_layer: str) -> dict:
    styled = [{**lyr, "id": f"l-{i}", "source": "v", "source-layer": source_layer} for i, lyr in enumerate(layers)]
    return {
        "version": 8,
        "glyphs": "https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf",
        "sources": {"v": {"type": "vector", "url": f"pmtiles://{pmtiles_url}"}},
        "layers": [{"id": "bg", "type": "background", "paint": {"background-color": "#f4efe1"}}, *styled],
    }


def _render_png(style: dict, bbox: list[float], out: str) -> None:
    """Render the composed style at the bbox → PNG via headless Chromium (Playwright). Raises on failure."""
    from playwright.sync_api import sync_playwright

    bb = json.dumps([bbox[0], bbox[1], bbox[2], bbox[3]])
    html = (f'<!doctype html><html><head><meta charset="utf-8">'
            f'<style>html,body,#map{{margin:0;padding:0;width:{THUMB_W}px;height:{THUMB_H}px;background:#f4efe1}}</style>'
            f'</head><body><div id="map"></div></body></html>')
    # Runs in the page: register pmtiles protocol, build the map, settle on idle (350 ms after the
    # bbox fit) or a 15 s hard cap, so we screenshot whatever painted.
    script = ("async () => { return await new Promise((resolve) => {"
              "  const protocol = new pmtiles.Protocol();"
              "  maplibregl.addProtocol('pmtiles', protocol.tile);"
              f"  const map = new maplibregl.Map({{ container:'map', style:{json.dumps(style)},"
              f"    interactive:false, attributionControl:false, fadeDuration:0, bounds:{bb},"
              "     fitBoundsOptions:{ padding:18, maxZoom:12, animate:false } });"
              "  let settled=false; const done=(v)=>{ if(!settled){ settled=true; resolve(v); } };"
              f"  map.on('load', ()=>{{ map.fitBounds({bb}, {{padding:18,maxZoom:12,animate:false,duration:0}});"
              "    map.once('idle', ()=> setTimeout(()=>done(true), 350)); });"
              "  map.on('error', (e)=> console.error('map error:', (e&&e.error&&e.error.message)||String(e)));"
              "  setTimeout(()=>done(true), 15000);"
              "}); }")
    with sync_playwright() as p:
        browser = p.chromium.launch(args=[
            "--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--hide-scrollbars",
            "--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist",
        ])
        try:
            page = browser.new_page(viewport={"width": THUMB_W, "height": THUMB_H}, device_scale_factor=2)
            page.set_default_timeout(RENDER_TIMEOUT_MS)
            page.set_content(html, wait_until="domcontentloaded")
            page.add_style_tag(content=_asset("maplibre-gl.css"))
            page.add_script_tag(content=_asset("pmtiles.js"))
            page.add_script_tag(content=_asset("maplibre-gl.js"))
            page.evaluate(script)
            page.locator("#map").screenshot(path=out)
        finally:
            browser.close()


def thumb_one(item: dict, force: bool = False) -> str:
    stem = item.get("id")
    if not stem:
        return "skip"
    _series_ctx.set(stem)
    pmtiles = ((item.get("assets") or {}).get("pmtiles") or {}).get("href")
    bbox = item.get("bbox")
    if not pmtiles or not bbox or len(bbox) < 4:
        hlog("no pmtiles/bbox — cannot render", step="resolve", level="NOTICE", category="expected")
        return "skip:nodata"

    # Validate bounding box coordinates to prevent MapLibre fitBounds uncaught exceptions
    west, south, east, north = bbox[0], bbox[1], bbox[2], bbox[3]
    if (west is None or south is None or east is None or north is None or
            not (-180.1 <= west <= 180.1) or not (-90.1 <= south <= 90.1) or
            not (-180.1 <= east <= 180.1) or not (-90.1 <= north <= 90.1)):
        hlog(f"invalid bbox coordinates: {bbox} (latitudes must be [-90, 90])",
             step="resolve", level="WARNING", category="attention")
        return "skip:invalid_bbox"

    style_url = _style_url(item)
    want_hash, style_bytes = _style_hash(style_url)
    png_obj, sha_obj = thumb_object(stem), sha_object(stem)

    # Items live NESTED at <collection>/<id>/<id>.json — not flat. Writing the flat path stamps a
    # stray object the catalog never references (so the viewer never sees the thumbnail).
    stac_path = f"{config.STAC_PREFIX}/{COLLECTION}/{stem}/{stem}.json"
    flat_stray = f"{config.STAC_PREFIX}/{COLLECTION}/{stem}.json"  # legacy mis-write to clean up
    thumb_href = config.public_url(png_obj)

    def ensure_stac_thumbnail() -> None:
        assets = item.setdefault("assets", {})
        if "thumbnail" not in assets or assets["thumbnail"].get("href") != thumb_href:
            assets["thumbnail"] = {
                "href": thumb_href,
                "type": "image/png",
                "roles": ["thumbnail"],
                "title": "Styled preview",
            }
            hlog(f"stamping thumbnail asset on STAC item JSON -> {stac_path}", step="stac")
            gcs.put_bytes(
                json.dumps(item, indent=2).encode("utf-8"),
                stac_path,
                content_type="application/json",
                cache_control=gcs.CACHE_MUTABLE,
            )
        # Self-heal: drop the flat stray an earlier build mis-wrote. `stem` is a topic id, never
        # "collection", so collection.json (same level) is never touched.
        if gcs.exists(flat_stray):
            gcs.delete(flat_stray)
            hlog(f"removed stray flat item {flat_stray}", step="stac")

    if not force and gcs.exists(png_obj) and gcs.exists(sha_obj):
        try:
            if gcs.get_bytes(sha_obj).decode().strip() == want_hash:
                hlog("thumbnail up to date (style unchanged)", step="resolve", level="NOTICE", category="expected")
                ensure_stac_thumbnail()
                return "skip:exists"
        except Exception:  # noqa: BLE001 — unreadable sidecar → re-render
            pass

    style = _compose_style(_layers_from(style_bytes), pmtiles, _source_layer(item))
    work = tempfile.mkdtemp(prefix=f"tt_{stem}_")
    try:
        out = os.path.join(work, "thumb.png")
        hlog(f"rendering ({'styled' if style_bytes else 'sand'})", step="render")
        try:
            _render_png(style, bbox, out)
        except Exception as e:  # noqa: BLE001
            hlog(f"FAIL render: {str(e).strip()[-300:]}", step="render", level="ERROR", category="attention", err=True)
            return "fail:render"
        if not os.path.exists(out) or os.path.getsize(out) == 0:
            hlog("FAIL no PNG produced", step="render", level="ERROR", category="attention", err=True)
            return "fail:nopng"

        gcs.upload(out, png_obj, content_type="image/png", cache_control=gcs.CACHE_MUTABLE)
        gcs.put_bytes(want_hash.encode(), sha_obj, content_type="text/plain", cache_control=gcs.CACHE_MUTABLE)
        hlog(f"OK thumbnail → {png_obj}", step="result", category="ok")
        ensure_stac_thumbnail()
        return "ok"
    finally:
        import shutil
        shutil.rmtree(work, ignore_errors=True)


def _topic_items() -> list[dict]:
    """Read every published ugs-serving-topics item.json (skip collection.json)."""
    out = []
    for path in gcs.list_paths(f"{config.STAC_PREFIX}/{COLLECTION}/"):
        if not path.endswith(".json") or path.endswith("/collection.json"):
            continue
        try:
            out.append(json.loads(gcs.get_bytes(path).decode()))
        except Exception:  # noqa: BLE001
            continue
    return out


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Render styled-preview thumbnails for vector serving-topics")
    ap.add_argument("item_id", nargs="*", default=[], help="Topic item id(s); default all")
    ap.add_argument("--all", action="store_true", help="All topics (default when no ids given)")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--force", action="store_true", help="Re-render even if the style is unchanged")
    args = ap.parse_args()

    items = _topic_items()
    if args.item_id:
        want = {s.strip() for s in args.item_id}
        items = [it for it in items if it.get("id") in want]
    items.sort(key=lambda it: it.get("id") or "")
    hlog(f"{len(items)} topics", step="startup")

    n = int(os.environ.get("CLOUD_RUN_TASK_COUNT", "1"))
    i = int(os.environ.get("CLOUD_RUN_TASK_INDEX", "0"))
    if n > 1:
        items = items[i::n]
        hlog(f"shard {i + 1}/{n}: {len(items)} topics", step="shard")
    if args.limit:
        items = items[:args.limit]

    tally = {"ok": 0, "expected": 0, "attention": 0}
    for it in items:
        res = thumb_one(it, force=args.force)
        tally[outcome_category(res)] += 1
    _series_ctx.set("")
    hlog(f"thumbnails complete: {tally['ok']} ok, {tally['expected']} skipped, "
         f"{tally['attention']} need attention", step="summary",
         level="WARNING" if tally["attention"] else "NOTICE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
