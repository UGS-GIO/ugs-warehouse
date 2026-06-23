# Styling — ugs-styles → warehouse → viewers, via STAC

**TL;DR:** authoritative MapLibre styling lives in the **`ugs-styles`** repo (cartographers' source
of truth). The warehouse binds it to layers **by STAC item id** and writes a `renders` block on each
item; the viewers read `item.renders`. No parallel style namespace, no hand-kept lookup table. A
style change re-binds via the **`restyle`** job — no reingest.

## How it works

```
ugs-styles (source of truth)
  build → dist-json/index.json (manifest, keyed by item id) + per-render GL JSON → styles CDN
        │
        ▼  warehouse fetches index.json once per ingest (core/styles.py)
ugs-warehouse (the bridge)
  at STAC emit, look up item.id in the manifest:
    vector style → renders.<id> = { title, assets:["pmtiles"], style_url } + a roles:["style"] asset
    raster render → renders.<id> = { title, assets:["cog"], colormap_name, rescale }
    no match     → item emits unchanged (graceful)
        │
        ▼  viewers read item.renders
viewers (this repo's viewer/ + ugs-map-viewer)
  fetch renders.default.style_url → { layers }; add the PMTiles source + each layer
```

Join key is the **STAC item id** (`topic.stem` for vector, `series_id` for pubs) — the same id the
catalog and both viewers already use. Styles CDN: `https://maps-assets.geology.utah.gov/styles/`
(`STYLES_INDEX_URL` default; env-overridable).

## Authoring a style (ugs-styles)

Each style module declares the item id it targets as data, validated at build:

```ts
export const binding = { itemId: "hazards_qfaults", kind: "vector", assets: ["pmtiles"] };
export default [ /* StyleLayer[] */ ];
```

`build-json.ts` reads `binding`, fails the build on a missing `itemId` / unknown `kind` / duplicate
`(itemId, render)`, and emits it into `index.json`. Folder names stay cartographer-friendly; the
machine identity is the declared `itemId`.

## Render kind by asset type

| Item asset | `kind` | `renders.<id>` carries | Rendered by |
|---|---|---|---|
| `pmtiles` (vector) | `vector` | `style_url` → GL fragment; paint on `source-layer` | MapLibre (client) |
| `cog` single-band (e.g. gravity) | `raster` | `colormap_name` / `rescale` / `nodata` | titiler (future) |
| `cog` RGB geologic plate (pubs) | — | nothing — the plate **is** the cartography | served as-is |

The render extension has no field for a vector GL style, so `style_url` is a local extension; the
warehouse also emits a `roles:["style"]` asset pointing at the same URL so asset-walking clients find it.

## Legend

The legend is derived from the bound style — no separate legend data, so it can't drift:
`match`/`step` color expressions, the per-category-layer shape (one flat-color layer + a `filter`
per class), or a single swatch for a uniform style. The warehouse also emits
`classification:classes` (value/name/color) on categorical items, which the viewer prefers when
present. Explicit legend entries (icon renders, e.g. wells by box type) override the derivation.

## Operating — `restyle` (rebind without a reingest)

A style change alters *how* a layer draws, not the data. `restyle` re-fetches the manifest and
re-runs the bind over the STAC items **already in GCS**, rewriting only the item.json files whose
`renders` changed. No DB read, no transform, no PMTiles — seconds. Source: `src/ugs_warehouse/restyle.py`.

```bash
python -m ugs_warehouse.restyle                    # rebind ugs-serving-topics (default scope)
python -m ugs_warehouse.restyle --collection all   # every collection, incl. pubs
python -m ugs_warehouse.restyle --dry-run          # report changes, write nothing
python -m ugs_warehouse.restyle --report           # diagnose binding (read-only)
python -m ugs_warehouse.restyle --refresh          # also rebuild collection.json + items.json
python -m ugs_warehouse.restyle --workers 16       # cap parallelism (default 32)
```

A style binds only when **both** hold (use `--report` to diagnose):

1. **id match** — `manifest.itemId` must equal the STAC `item.id` exactly (case-sensitive). A style
   under a renamed/typo'd id is an **orphan** — never binds.
2. **asset match** — `manifest.assets` (e.g. `["pmtiles"]`) must intersect the item's actual asset
   keys. Otherwise it's an **asset-miss**.

The default scope is `ugs-serving-topics` only (pubs skipped unless `--collection all`). Intended to
fire from the `ugs-styles` publish (after the CDN rsync) so a style edit reaches the viewers hands-free.
