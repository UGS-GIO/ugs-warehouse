"""Bridge ugs-styles -> the item's `ugs:renders` block + `style` asset.

`ugs-styles` (neighbor repo) is the authoritative system of record for how vector layers are
styled: it builds MapLibre GL JSON per layer/render + an `index.json` manifest, published
CDN-only. The warehouse fetches that manifest once per run, looks a topic up by its STAC item
id, and returns a `renders` block (+ a roles:["style"] asset for the default vector render).

The block is emitted as `ugs:renders` (UGS-prefixed), NOT the STAC render extension: that
extension is raster/titiler-oriented and its v2.0.0 schema requires a `rel:"render"` image link
when web-map-links is present — we render vector GL client-side and have none. See
`core.stac.attach_renders`. The `roles:["style"]` asset is the standard, interoperable pointer.

Join key is the STAC item id (docs/STYLING.md). Manifest entries are expected to carry
`itemId`; older/transitional manifests keyed by `layer` are tolerated. Render kind follows the
target asset: vector -> a GL `style_url`; raster -> colormap/rescale (titiler-style fields).

Graceful by design: an unreachable or empty manifest yields no renders, so items emit exactly
as they do today (the viewer falls back to its own neutral render).
"""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.request
from functools import lru_cache

from . import config, gcs

# How long a fetched manifest may be reused. The cache exists so a 64-thread ingest fetches once,
# not 64 times — it is NOT meant to pin a snapshot for the life of a long run. Without a bound, an
# ingest that starts before a style publish keeps re-attaching the pre-publish legend for hours,
# overwriting whatever the rebind just wrote.
_TTL_SECONDS = 60.0
_lock = threading.Lock()
_cache: tuple[float, tuple[dict, ...]] | None = None


def _parse(raw: bytes) -> tuple[dict, ...]:
    """Item-bound entries only. A `collectionId` entry styles a federated collection (UBM's datacubes),
    which its own catalog attaches; the warehouse mints no such items, so it would only read as an orphan."""
    data = json.loads(raw.decode())
    if not isinstance(data, list):
        return ()
    return tuple(e for e in data if isinstance(e, dict) and not e.get("collectionId"))


def _fetch() -> tuple[dict, ...]:
    """Read the manifest, preferring the GCS object over its cached CDN view.

    The object is authoritative and has no edge in front of it, so a rebind triggered seconds after
    a publish still sees the new manifest. HTTPS stays as the fallback for anything running without
    bucket credentials (local tooling, tests).
    """
    try:
        return _parse(gcs.get_bytes(config.STYLES_INDEX_OBJECT))
    except Exception:  # noqa: BLE001 — no creds / object missing: fall back to the public CDN copy
        pass
    try:
        with urllib.request.urlopen(config.STYLES_INDEX_URL, timeout=10) as resp:  # noqa: S310 (https CDN)
            return _parse(resp.read())
    except Exception:  # noqa: BLE001 — styling is best-effort; never sink an ingest
        return ()


def _manifest() -> tuple[dict, ...]:
    """The ugs-styles manifest, cached for `_TTL_SECONDS`. Returns () on any failure (graceful)."""
    global _cache
    with _lock:
        if _cache and (time.monotonic() - _cache[0]) < _TTL_SECONDS:
            return _cache[1]
        entries = _fetch()
        _cache = (time.monotonic(), entries)
        return entries


def refresh() -> int:
    """Drop the cached manifest and re-read it. Returns entry count."""
    global _cache
    with _lock:
        _cache = None
    return len(_manifest())


def warm() -> int:
    """Prime the manifest cache (call once before a parallel ingest). Returns entry count."""
    return len(_manifest())


def _entry_key(entry: dict) -> str:
    """Join key for a manifest entry — the item id, tolerating older `layer`-keyed manifests."""
    return str(entry.get("itemId") or entry.get("layer") or "")


def entry_for(item_id: str) -> dict | None:
    """The manifest entry bound to `item_id`, or None. The manifest belongs to ugs-styles and is
    read-only here, so a caller retiring an item can only report the binding, not remove it."""
    return next((e for e in _manifest() if _entry_key(e) == item_id), None)


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
            # Label renders carry the glyph template; without it MapLibre draws no text at all.
            glyphs = str(entry.get("glyphs") or "").lstrip("/")
            if glyphs:
                block["glyphs"] = f"{config.STYLES_CDN_BASE}/{glyphs}"
            # Explicit legend (icon renders have no derivable paint color) — pass through verbatim.
            if entry.get("legend"):
                block["legend"] = entry["legend"]
            # The attribute this render symbolizes — lets the viewer wire the legend/filter to a
            # field without re-parsing the GL expression. Pass through verbatim when declared.
            if entry.get("field"):
                block["field"] = entry["field"]
            if render == "default" and style_asset is None:
                style_asset = {
                    "href": url, "type": "application/json", "roles": ["style"],
                    "title": "MapLibre GL style (default render)",
                }
        renders[render] = block
    return renders, style_asset


# ---------------------------------------------------------------- classification
# Derive categorical classes (value/label + color) from the bound GL style fragment, so the catalog
# carries machine-readable categories (classification extension) instead of clients reverse-
# engineering the paint. Mirrors the viewer's legend derivation: `match`/`step` color expressions,
# else the per-category-layer shape (one flat-color layer + a `filter` per class). Uniform styles
# (one color, no categories) are NOT a classification → no classes.

@lru_cache(maxsize=128)
def _fetch_layers(style_url: str) -> tuple:
    """Fetch a GL style fragment's `layers` (cached per URL). () on any failure (graceful)."""
    try:
        with urllib.request.urlopen(style_url, timeout=10) as resp:  # noqa: S310 (https CDN)
            data = json.loads(resp.read().decode())
        layers = data.get("layers") if isinstance(data, dict) else None
        return tuple(lyr for lyr in (layers or []) if isinstance(lyr, dict))
    except Exception:  # noqa: BLE001 — classification is best-effort; never sink an ingest
        return ()


_FLAT_COLOR_KEYS = ("fill-color", "circle-color", "line-color", "icon-color", "text-color")


def _flat_color(paint: dict) -> str | None:
    for k in _FLAT_COLOR_KEYS:
        v = paint.get(k)
        if isinstance(v, str):
            return v
    return None


# Comparison filters — the shape ugs-styles' `graduated` archetype emits for a *continuous* field,
# where each class is a half-open bin (`>=` lower, `<` upper) rather than an exact value. Symbols
# match the `step` labels above so both routes to a graduated legend read alike.
_CMP_SYMBOL = {">=": "≥", ">": ">", "<=": "≤", "<": "<"}
_CMP_MIRROR = {">=": "<=", ">": "<", "<=": ">=", "<": ">"}


def _comparison(f) -> tuple[str, object] | None:
    """(op, literal) for `['>=', ['get', 'p'], 0.2]`, normalized so the literal is the right-hand
    side (`['<', 0.2, ['get', 'p']]` mirrors to `('>', 0.2)`). None if not a comparison."""
    if not (isinstance(f, list) and len(f) >= 3 and f[0] in _CMP_SYMBOL):
        return None
    a, b = f[1], f[2]
    if isinstance(a, list) and not isinstance(b, list):
        return f[0], b
    if isinstance(b, list) and not isinstance(a, list):
        return _CMP_MIRROR[f[0]], a
    return None


def _label_from_filter(f) -> str | None:
    """Category label from a layer filter: ==, in, match, comparisons, any/all (recurses)."""
    if not isinstance(f, list) or not f:
        return None
    op = f[0]
    if op == "==" and len(f) >= 3:
        a, b = f[1], f[2]
        lit = a if not isinstance(a, list) else b if not isinstance(b, list) else None
        return None if lit is None else str(lit)
    if op == "in" and len(f) >= 2:
        needle = f[1]
        if not isinstance(needle, list):
            return str(needle)
        vals = [v for v in f[2:] if not isinstance(v, list)]
        return ", ".join(map(str, vals)) if vals else None
    if op == "match" and len(f) >= 3:
        vals = f[2]
        return ", ".join(map(str, vals)) if isinstance(vals, list) else str(vals)
    if op in _CMP_SYMBOL:
        cmp = _comparison(f)
        return None if cmp is None else f"{_CMP_SYMBOL[cmp[0]]} {cmp[1]}"
    if op in ("all", "any"):
        # Equality-shaped subfilters win, so an enumerated class keeps its plain value label.
        for sub in f[1:]:
            if isinstance(sub, list) and sub and sub[0] not in _CMP_SYMBOL:
                r = _label_from_filter(sub)
                if r:
                    return r
        # Otherwise fold the comparisons: a lower+upper pair is one bin ("0.2 – 0.4"), a lone
        # bound is the open-ended first/last class.
        bounds = [c for c in (_comparison(sub) for sub in f[1:]) if c]
        lo = next((v for o, v in bounds if o in (">=", ">")), None)
        hi = next((v for o, v in bounds if o in ("<", "<=")), None)
        if lo is not None and hi is not None:
            return f"{lo} – {hi}"
        if bounds:
            o, v = bounds[0]
            return f"{_CMP_SYMBOL[o]} {v}"
    return None


def _derive_classes(layers: tuple) -> list[tuple[str, str]]:
    """(label, color) pairs from the style, or [] for non-categorical/uniform styles."""
    # 1. data-driven paint (match / step) on a single layer
    for layer in layers:
        for key, v in (layer.get("paint") or {}).items():
            if not (isinstance(key, str) and key.endswith("-color") and isinstance(v, list)):
                continue
            if v[0] == "match":
                pairs = v[2:]
                has_fb = len(pairs) % 2 == 1
                body = pairs[:-1] if has_fb else pairs
                out = [(str(body[i]), str(body[i + 1])) for i in range(0, len(body) - 1, 2)]
                if has_fb:
                    out.append(("Other", str(pairs[-1])))
                if out:
                    return out
            if v[0] == "step" and len(v) >= 4:
                out = [(f"< {v[3]}", str(v[2]))]
                for i in range(3, len(v) - 1, 2):
                    out.append((f"≥ {v[i]}", str(v[i + 1])))
                if out:
                    return out
    # 2. per-category-layer shape (flat color + filter)
    out = [(_label_from_filter(layer.get("filter")), _flat_color(layer.get("paint") or {}))
           for layer in layers]
    cats = [(lbl, col) for lbl, col in out if lbl and col]
    return cats  # [] when uniform (no filters) → not a classification


def _color_hint(color: str) -> str | None:
    """`#7B1FA2` → `7B1FA2` (classification color_hint = 6 hex, no #). None if not hex."""
    c = color.lstrip("#")
    return c.upper() if re.fullmatch(r"[0-9a-fA-F]{6}", c) else None


def _class_name(label: str, i: int) -> str:
    """A classification:classes `name` token. The extension constrains `name` to ^[0-9A-Za-z-_]+$
    (a machine token, NOT a human label — that's `title`), so slugify and fall back to class_<i>."""
    slug = re.sub(r"[^0-9A-Za-z_-]+", "_", label).strip("_")
    return slug or f"class_{i}"


def classification_classes(style_url: str) -> list[dict]:
    """`classification:classes` for the item's default vector render, or [] (graceful)."""
    classes: list[dict] = []
    for i, (label, color) in enumerate(_derive_classes(_fetch_layers(style_url))):
        # `name` is the machine token (regex-constrained); `title` carries the human label.
        cls: dict = {"value": i, "name": _class_name(str(label), i), "title": str(label)}
        hint = _color_hint(color)
        if hint:
            cls["color_hint"] = hint
        classes.append(cls)
    return classes
