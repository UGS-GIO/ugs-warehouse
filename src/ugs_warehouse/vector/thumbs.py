"""Styled-preview thumbnails for vector serving-topics — a PNG of the PMTiles layer rendered with its
bound MapLibre style (or a neutral "sand" style when unstyled), so the catalog shows a real preview.

Pubs get cover thumbnails from a PDF page (`pubs/thumbs.py`); topics are vector + style, so they can't
be rasterised the same way. MapLibre needs WebGL, so we render in headless Chromium — but driven from
Python via Playwright (no separate Node project): the browser runs the SAME maplibre-gl + pmtiles libs
the viewer uses (vendored in `render_assets/`), so thumbnails are WYSIWYG. We compose the full style
exactly like the viewer (viewer/src/Map.tsx): the ugs-styles JSON is layers-ONLY, so we wrap it into a
version-8 style with a vector source over `pmtiles://`, a sand background, and `source`/`source-layer`
injected per layer.

Content-addressed + idempotent: each PNG carries a `.sha` sidecar of `RENDERER_VERSION + the layers it
draws`. A run skips a topic whose sidecar matches → a NEW layer renders (no PNG yet), a SYMBOLOGY change
re-renders (drawn layers change), an unchanged topic is skipped. It runs nightly from Cloud Scheduler
(scripts/provision.sh) and only does work when something changed. Sharded via
CLOUD_RUN_TASK_INDEX/COUNT, same as the pubs jobs.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import traceback
import urllib.request

from ..core import config, gcs, stac
from ..pubs.harvest import _series_ctx, hlog, outcome_category

CATALOG = stac.SERVING_TOPICS_CATALOG  # items nest one level under it, per mart schema
# Bump to force a global re-render for a renderer change the drawn layers don't show (the hash
# already covers the layers, so a restyle or a SAND_LAYERS edit needs no bump).
RENDERER_VERSION = "1"
RENDER_TIMEOUT_MS = int(os.environ.get("TOPIC_THUMB_TIMEOUT_MS", "30000"))
THUMB_W = int(os.environ.get("TOPIC_THUMB_W", "480"))
THUMB_H = int(os.environ.get("TOPIC_THUMB_H", "320"))

_ASSETS = os.path.join(os.path.dirname(__file__), "render_assets")

# The viewer's geometry gates for its unstyled fallback (GEOM_FILTER, viewer/src/map/map-model.ts).
# Without them the circle layer puts a dot on every polygon and line vertex.
GEOM_FILTER = {
    "fill": ["match", ["geometry-type"], ["Polygon", "MultiPolygon"], True, False],
    "line": ["match", ["geometry-type"], ["LineString", "MultiLineString", "Polygon", "MultiPolygon"], True, False],
    "point": ["match", ["geometry-type"], ["Point", "MultiPoint"], True, False],
}

# Neutral fallback cartography (sand) — used when a topic has no bound style, or its style is purely
# symbol/label layers (dropped below). Covers fill / line / circle so any geometry type shows.
SAND_LAYERS = [
    {"id": "d-fill", "type": "fill", "filter": GEOM_FILTER["fill"],
     "paint": {"fill-color": "#d8c39a", "fill-opacity": 0.55, "fill-outline-color": "#7a5c2e"}},
    {"id": "d-line", "type": "line", "filter": GEOM_FILTER["line"],
     "paint": {"line-color": "#7a5c2e", "line-width": 1.1}},
    {"id": "d-circle", "type": "circle", "filter": GEOM_FILTER["point"],
     "paint": {"circle-color": "#9c6b30", "circle-radius": 3.2,
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


def _drawn_layers(style_url: str | None) -> list[dict]:
    """The layers a thumbnail draws: the bound style's minus symbol/label layers (they need glyphs and
    sprites we don't carry), else the sand fallback, which a symbol-only style also gets. A bound
    style that won't fetch, or isn't a style object (an error page served with a 200), raises instead:
    a sand preview would replace the real one on a CDN blip and swap back the next night."""
    if not style_url:
        return SAND_LAYERS
    doc = json.loads(_fetch(style_url))
    if not isinstance(doc, dict):
        raise ValueError(f"style is a JSON {type(doc).__name__}, not an object")
    return [lyr for lyr in (doc.get("layers") or []) if lyr.get("type") != "symbol"] or SAND_LAYERS


def _layers_hash(layers: list[dict]) -> str:
    """Content address of a thumbnail: the renderer version plus exactly the layers drawn, so a
    restyle or an edit to the fallback re-renders the topics it affects and nothing else."""
    drawn = json.dumps(layers, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{RENDERER_VERSION}\n{drawn}".encode()).hexdigest()


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


def thumb_one(item: dict, stac_path: str, force: bool = False) -> tuple[str, dict | None]:
    """Render + stamp one topic whose item lives at `stac_path`. Returns (outcome code, the thumbnail
    asset this run put or confirmed on the item; None on skip and failure paths)."""
    stem = item["id"]
    _series_ctx.set(stem)
    pmtiles = ((item.get("assets") or {}).get("pmtiles") or {}).get("href")
    bbox = item.get("bbox")
    if not pmtiles or not bbox or len(bbox) < 4:
        hlog("no pmtiles/bbox — cannot render", step="resolve", level="NOTICE", category="expected")
        return "skip:nodata", None

    # A bbox outside lon/lat means the item's CRS is mislabeled (the viewer would misplace it too),
    # and it would throw in MapLibre's fitBounds.
    west, south, east, north = bbox[0], bbox[1], bbox[2], bbox[3]
    if (west is None or south is None or east is None or north is None or
            not (-180.1 <= west <= 180.1) or not (-90.1 <= south <= 90.1) or
            not (-180.1 <= east <= 180.1) or not (-90.1 <= north <= 90.1)):
        hlog(f"FAIL invalid bbox coordinates: {bbox} (must be WGS84 lon/lat)",
             step="resolve", level="ERROR", category="attention", err=True)
        return "fail:invalid_bbox", None

    style_url = _style_url(item)
    try:
        layers = _drawn_layers(style_url)
    except Exception as e:  # noqa: BLE001 — reported as a failure, never rendered as sand
        hlog(f"FAIL style {style_url}: {type(e).__name__}: {(str(e).splitlines() or [''])[0]}",
             step="resolve", level="ERROR", category="attention", err=True)
        return "fail:style", None
    want_hash = _layers_hash(layers)
    png_obj, sha_obj = thumb_object(stem), sha_object(stem)
    # Legacy mis-write to clean up: an earlier build stamped a flat item the catalog never references.
    flat_stray = f"{config.STAC_PREFIX}/{_collection_path(item)}/{stem}.json"
    thumb_href = config.public_url(png_obj)

    def ensure_stac_thumbnail(meta: gcs.FileMeta | None = None) -> tuple[str, dict | None]:
        """Stamp onto the item as it is in GCS at write time, not the copy read before rendering:
        writing that back would revert an ingest that landed mid-render, or re-create a topic retired
        meanwhile. The gap left is this read to its write, as for every other item writer here.
        Returns ("stamped" | "current" | "gone", the thumbnail asset)."""
        try:
            fresh = json.loads(gcs.get_bytes(stac_path).decode())
        except FileNotFoundError:
            return "gone", None
        assets = fresh.setdefault("assets", {})
        current = assets.get("thumbnail") or {}
        # Merged onto whatever is there, so the up-to-date path (no `meta`, nothing rendered) keeps
        # the file:size/file:checksum an earlier render stamped instead of dropping them. Same asset
        # the ingest stamps (vector/sink_stac.py), usage hint included.
        want = {**current, "href": thumb_href, "type": "image/png", "roles": ["thumbnail"],
                "title": "Styled preview", "description": stac.USAGE_THUMBNAIL, **stac.file_fields(meta)}
        state = "current"
        if current != want:
            assets["thumbnail"] = want
            hlog(f"stamping thumbnail asset on STAC item JSON -> {stac_path}", step="stac")
            # Same headers as stac.write_item, so a stamped item doesn't flip to no-cache.
            gcs.put_bytes(json.dumps(fresh, indent=2).encode("utf-8"), stac_path,
                          content_type="application/geo+json", cache_control=gcs.CACHE_CATALOG)
            state = "stamped"
        # Self-heal: drop the flat stray an earlier build mis-wrote. `stem` is a topic id, never
        # "collection", so collection.json (same level) is never touched.
        if gcs.exists(flat_stray):
            gcs.delete(flat_stray)
            hlog(f"removed stray flat item {flat_stray}", step="stac")
        return state, want

    up_to_date = False
    if not force and gcs.exists(png_obj) and gcs.exists(sha_obj):
        try:
            up_to_date = gcs.get_bytes(sha_obj).decode().strip() == want_hash
        except Exception:  # noqa: BLE001 — unreadable sidecar → re-render
            up_to_date = False
    if up_to_date:
        hlog("thumbnail up to date (style unchanged)", step="resolve", level="NOTICE", category="expected")
        state, asset = ensure_stac_thumbnail()
        return ("skip:gone", None) if state == "gone" else ("skip:exists", asset)

    style = _compose_style(layers, pmtiles, _source_layer(item))
    work = tempfile.mkdtemp(prefix=f"tt_{stem}_")
    try:
        out = os.path.join(work, "thumb.png")
        hlog(f"rendering ({'sand' if layers is SAND_LAYERS else 'styled'})", step="render")
        try:
            _render_png(style, bbox, out)
        except Exception as e:  # noqa: BLE001
            hlog(f"FAIL render: {str(e).strip()[-300:]}", step="render", level="ERROR", category="attention", err=True)
            return "fail:render", None
        if not os.path.exists(out) or os.path.getsize(out) == 0:
            hlog("FAIL no PNG produced", step="render", level="ERROR", category="attention", err=True)
            return "fail:nopng", None

        # Drop the old sidecar first: a --force re-render of an unchanged style would otherwise leave
        # one that still matches, and a stamp that then failed would never be redone.
        gcs.delete(sha_obj)
        meta = gcs.upload(out, png_obj, content_type="image/png", cache_control=gcs.CACHE_MUTABLE)
        state, asset = ensure_stac_thumbnail(meta)
        if state == "gone":
            # Retired while rendering: take back the thumbnail so nothing outlives the topic (a later
            # topic with this id would otherwise be bound to it). gcs.delete swallows errors, so check.
            gcs.delete(png_obj)
            gcs.delete(sha_obj)
            if gcs.exists(png_obj) or gcs.exists(sha_obj):
                hlog(f"FAIL topic retired mid-render but its thumbnail would not delete: {png_obj}",
                     step="result", level="ERROR", category="attention", err=True)
                return "fail:cleanup", None
            hlog("topic retired mid-render; dropped its new thumbnail", step="result",
                 level="NOTICE", category="expected")
            return "skip:gone", None
        # The sidecar marks this render as done, so it goes last: a stamp that failed renders again.
        gcs.put_bytes(want_hash.encode(), sha_obj, content_type="text/plain", cache_control=gcs.CACHE_MUTABLE)
        hlog(f"OK thumbnail → {png_obj}", step="result", category="ok")
        return "ok", asset
    finally:
        import shutil
        shutil.rmtree(work, ignore_errors=True)


def _collection_path(item: dict) -> str:
    """Layout path of the item's sub-collection — `ugs-serving-topics/<mart schema>`.

    The schema rides on the item as `ugs:dbt_schema` (written by sink_stac), so a thumbnail run
    doesn't need the Postgres registry to find where an item lives."""
    schema = ((item.get("properties") or {}).get("ugs:dbt_schema") or "").strip()
    return f"{CATALOG}/{schema}" if schema else CATALOG


def _stem(path: str) -> str:
    return path.split("/")[-2]


def _topic_item_paths() -> list[str]:
    """Every published serving-topic item.json, in topic-id order (skip catalog/collection/items
    docs). Items are at `<catalog>/<schema>/<id>/<id>.json`; anything else under the prefix is a
    generated index document."""
    paths = []
    for path in gcs.list_paths(f"{config.STAC_PREFIX}/{CATALOG}/"):
        parts = path.split("/")
        if path.endswith(".json") and len(parts) >= 2 and parts[-1] == f"{parts[-2]}.json":
            paths.append(path)
    return sorted(paths, key=_stem)


def thumb_path(path: str, force: bool = False) -> tuple[str, dict | None]:
    """Read one topic's item and thumbnail it where it lives. Gone since the listing means it was
    retired; an item that won't read or parse, or whose id isn't its path's, is a failure, because
    skipping it quietly lets a run pass while topics go without previews."""
    stem = _stem(path)
    _series_ctx.set(stem)
    try:
        item = json.loads(gcs.get_bytes(path).decode())
    except FileNotFoundError:
        hlog("item gone since the listing (retired)", step="resolve", level="NOTICE", category="expected")
        return "skip:gone", None
    except Exception as e:  # noqa: BLE001 — reported as a failure, never skipped
        hlog(f"FAIL unreadable item {path}: {type(e).__name__}: {(str(e).splitlines() or [''])[0]}",
             step="resolve", level="ERROR", category="attention", err=True)
        return "fail:unreadable", None
    if item.get("id") != stem:
        hlog(f"FAIL item id {item.get('id')!r} does not match its path {path}",
             step="resolve", level="ERROR", category="attention", err=True)
        return "fail:layout", None
    return thumb_one(item, path, force=force)


# The rollup the viewer lists layers from.
ROLLUP_INDEX = f"{config.STAC_PREFIX}/{CATALOG}/items.json"


def _index_lags(thumbnails: dict[str, dict]) -> bool:
    """Whether the rollup index is missing any of these thumbnails. Checked against GCS, not against
    what this run wrote: a crash before the refresh, or another shard's refresh landing last, leaves
    the index behind while every item is current. A re-render that only changed file:size needs no
    refresh, since the index doesn't carry it. An index that won't read counts as behind, because
    the refresh is what rewrites it."""
    if not thumbnails:
        return False
    try:
        index = json.loads(gcs.get_bytes(ROLLUP_INDEX).decode())
        listed = {e.get("id"): (e.get("assets") or {}).get("thumbnail") or {} for e in index.get("items") or []}
    except FileNotFoundError:
        return True
    except Exception as e:  # noqa: BLE001 — logged; the refresh repairs it or fails loudly itself
        hlog(f"unreadable {ROLLUP_INDEX}: {type(e).__name__}: {(str(e).splitlines() or [''])[0]}",
             step="catalog", level="ERROR", category="attention", err=True)
        return True
    keys = stac.INDEX_ASSET_KEYS
    return any({k: listed.get(stem, {}).get(k) for k in keys} != {k: asset.get(k) for k in keys}
               for stem, asset in thumbnails.items())


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Render styled-preview thumbnails for vector serving-topics")
    ap.add_argument("item_id", nargs="*", default=[], help="Topic item id(s); default all")
    ap.add_argument("--all", action="store_true", help="All topics (default when no ids given)")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--force", action="store_true", help="Re-render even if the style is unchanged")
    args = ap.parse_args()

    paths = _topic_item_paths()
    if args.item_id:
        want = {s.strip() for s in args.item_id}
        paths = [p for p in paths if _stem(p) in want]
    hlog(f"{len(paths)} topics", step="startup")

    n = int(os.environ.get("CLOUD_RUN_TASK_COUNT", "1"))
    i = int(os.environ.get("CLOUD_RUN_TASK_INDEX", "0"))
    if n > 1:
        paths = paths[i::n]
        hlog(f"shard {i + 1}/{n}: {len(paths)} topics", step="shard")
    if args.limit:
        paths = paths[:args.limit]

    tally = {"ok": 0, "expected": 0, "attention": 0}
    rc = 0
    thumbnails: dict[str, dict] = {}
    for path in paths:
        try:
            res, asset = thumb_path(path, force=args.force)
        except Exception as e:  # noqa: BLE001 — one topic's failure must not stop the rest of the shard
            tail = "".join(traceback.format_exception(e)[-3:]).strip()
            hlog(f"FAIL {type(e).__name__}: {tail[-800:]}", step="error",
                 level="ERROR", category="attention", err=True)
            res, asset = "fail:error", None
        tally[outcome_category(res)] += 1
        if res.startswith("fail"):
            rc = 1
        if asset:
            thumbnails[_stem(path)] = asset
    _series_ctx.set("")
    hlog(f"thumbnails complete: {tally['ok']} ok, {tally['expected']} skipped, "
         f"{tally['attention']} need attention", step="summary",
         level="WARNING" if tally["attention"] else "NOTICE")
    if _index_lags(thumbnails):
        # Only refresh_catalog rebuilds items.json; without this a new thumbnail waits for whichever
        # ingest happens to run next.
        hlog("items.json is missing thumbnails; refreshing the catalog", step="catalog")
        stac.refresh_catalog()
        # Checked again so a mismatch a refresh can't fix alerts instead of repeating every night. A
        # race with another shard's refresh clears on the task retry.
        if _index_lags(thumbnails):
            hlog("FAIL items.json still missing thumbnails after a refresh", step="catalog",
                 level="ERROR", category="attention", err=True)
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
