"""Bridge ugs-styles -> the STAC render extension.

`ugs-styles` (neighbor repo) is the authoritative system of record for how vector layers are
styled: it builds MapLibre GL JSON per layer/render + an `index.json` manifest, published
CDN-only. The warehouse fetches that manifest once per run, looks a topic up by its STAC item
id, and returns a `renders` block (+ a roles:["style"] asset for the default vector render).

Join key is the STAC item id (docs/STYLING.md). Manifest entries are expected to carry
`itemId`; older/transitional manifests keyed by `layer` are tolerated. Render kind follows the
target asset: vector -> a GL `style_url`; raster -> colormap/rescale (standard render fields).

Graceful by design: an unreachable or empty manifest yields no renders, so items emit exactly
as they do today (the viewer falls back to its own neutral render).
"""
from __future__ import annotations

import json
import urllib.request
from functools import lru_cache

from . import config

# render extension — declares the `renders` object on item properties.
RENDER_EXT = "https://stac-extensions.github.io/render/v1.0.0/schema.json"


@lru_cache(maxsize=1)
def _manifest() -> tuple[dict, ...]:
    """Fetch + cache the ugs-styles manifest. Returns () on any failure (graceful)."""
    try:
        with urllib.request.urlopen(config.STYLES_INDEX_URL, timeout=10) as resp:  # noqa: S310 (https CDN)
            data = json.loads(resp.read().decode())
        return tuple(e for e in data if isinstance(e, dict)) if isinstance(data, list) else ()
    except Exception:  # noqa: BLE001 — styling is best-effort; never sink an ingest
        return ()


def warm() -> int:
    """Prime the manifest cache (call once before a parallel ingest). Returns entry count."""
    return len(_manifest())


def _entry_key(entry: dict) -> str:
    """Join key for a manifest entry — the item id, tolerating older `layer`-keyed manifests."""
    return str(entry.get("itemId") or entry.get("layer") or "")


def _style_url(entry: dict) -> str:
    return f"{config.STYLES_CDN_BASE}/{str(entry.get('path') or '').lstrip('/')}"


def renders_for(item_id: str, asset_keys: set[str]) -> tuple[dict, dict | None]:
    """`(renders, style_asset)` for an item.

    `renders` is `{render_id: {...}}` (empty when nothing matches). `style_asset` is the default
    vector render's GL fragment as a roles:["style"] STAC asset, or None. A render is attached
    only when the item actually carries the asset it targets.
    """
    renders: dict = {}
    style_asset: dict | None = None
    for entry in _manifest():
        if _entry_key(entry) != item_id:
            continue
        render = str(entry.get("render") or "default")
        kind = str(entry.get("kind") or "vector")
        assets = list(entry.get("assets") or (["cog"] if kind == "raster" else ["pmtiles"]))
        if asset_keys and not (set(assets) & asset_keys):
            continue  # the targeted asset isn't on this item — skip
        block: dict = {"title": entry.get("title") or render, "assets": assets}
        if kind == "raster":
            for k in ("colormap_name", "colormap", "rescale", "nodata"):
                if entry.get(k) is not None:
                    block[k] = entry[k]
        else:
            url = _style_url(entry)
            block["style_url"] = url
            # Icon renders (e.g. UCRC wells by box type) carry a pre-baked pie-wedge sprite sheet;
            # the viewer map.addSprite()s this base URL before applying the symbol layer.
            sprite = str(entry.get("sprite") or "").lstrip("/")
            if sprite:
                block["sprite"] = f"{config.STYLES_CDN_BASE}/{sprite}"
            if render == "default" and style_asset is None:
                style_asset = {
                    "href": url, "type": "application/json", "roles": ["style"],
                    "title": "MapLibre GL style (default render)",
                }
        renders[render] = block
    return renders, style_asset
