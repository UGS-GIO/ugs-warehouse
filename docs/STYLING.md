# Styling — binding ugs-styles to warehouse layers via STAC

**Status:** design (2026-06-15). Implements the roadmap `☐ Legend + symbology` item.
**Repos touched:** `ugs-styles` (source of truth), `ugs-warehouse` (bridge), `ugs-map-viewer`
+ this repo's `viewer/` (consumers).

Today every layer renders as one random flat color (`viewer/src/Map.tsx` `colorFor`,
`fill-opacity 0.15`). For geologic data that is unusable. This doc defines how authoritative
MapLibre styling flows from `ugs-styles` to every map, through STAC, with **no parallel
namespace and no hand-kept lookup table**.

---

## 1. Principles

1. **One identity: the STAC item id.** The warehouse already mints a unique item id per layer
   from the topic (`topic.stem`, e.g. `hazards_qfaults`); pubs use `series_id`. That id is the
   canonical name of a layer across the catalog and both viewers. We do **not** introduce a
   second "style slug" namespace — `ugs-styles` keys on the item id.

2. **One binding point: the `renders` extension on the item, scoped to an asset.** A style does
   not attach to a layer abstractly — it attaches to a *renderable asset* (`pmtiles` for vector,
   `cog` for raster). The STAC [render extension](https://github.com/stac-extensions/render)
   models exactly this (`renders.<id>.assets = [...]`).

3. **Render kind follows asset type.** Vector tiles take a MapLibre GL style fragment; single-band
   rasters take a colormap + rescale; pre-rendered RGB rasters take nothing. One mechanism, three
   fills (§4).

4. **The warehouse is the only STAC author.** `ugs-styles` stays a pure style library with zero
   STAC knowledge. The warehouse, at emit time, knows the item id and which assets exist, so it
   writes the `renders` block by looking the item id up in the `ugs-styles` manifest. Consumers
   (both viewers) only ever read `item.renders`.

5. **CNG-pure: static JSON behind a CDN, zero server compute** for vector. (Raster colormaps are
   consumed by a future titiler; the *binding* is still static.)

---

## 2. The flow

```
ugs-styles  (source of truth — cartographers work here)
  src/styles/<dir>/<render>.ts   → exports StyleLayer[] + a binding { itemId, kind, assets }
  npm run build:json             → dist-json/styles/<dir>/<render>.json
                                    dist-json/index.json   (manifest, keyed by item id)
  CI rsync                       → styles CDN bucket
        │
        ▼  (warehouse fetches index.json once per run)
ugs-warehouse  (the bridge — NEW: core/styles.py)
  at STAC emit, for each item:
    look up item.id in the manifest
    if a vector style exists  → renders.<id> = { title, assets:["pmtiles"], style_url }
                                + a roles:["style"] asset (conventional, discoverable)
    if a raster render exists  → renders.<id> = { title, assets:["cog"], colormap_name, rescale }
    no match                   → emit item unchanged (graceful — same as today)
        │
        ▼  (both viewers read item.renders)
viewers  (this repo's viewer/ + ugs-map-viewer)
  fetch renders.default.style_url → { layers }
  addSource(pmtiles) ; for each layer: addLayer({ ...layer, source, source-layer })
  replaces the flat colorFor path
```

`ugs-styles` already documents the `renders` + `index.json` contract; this design pins the **key**
to the STAC item id and adds the raster case.

---

## 3. The join key — why declared front-matter (decided)

The slug-matching problem exists only because `ugs-styles` currently uses its own layer names
(`enmin_ucrc_wells`, `hazards-displacement-contours`) parallel to the warehouse's item ids. We
remove that namespace. Each style module **declares the item id it targets**, as data:

```ts
// src/styles/hazards_qfaults/default.ts
export const binding = {
  itemId: "hazards_qfaults",   // == STAC item id (warehouse topic.stem)
  kind: "vector",              // "vector" | "raster"
  assets: ["pmtiles"],         // STAC asset(s) this render draws
};
export default [ /* StyleLayer[] */ ];
```

`build-json.ts` reads `binding`, **validates** (fail the build on a missing `itemId`, an unknown
`kind`, or a duplicate `(itemId, render)` pair), and emits it into the manifest:

```json
// dist-json/index.json
[
  { "itemId": "hazards_qfaults", "render": "default", "kind": "vector",
    "assets": ["pmtiles"], "path": "styles/hazards_qfaults/default.json" }
]
```

Why this over the alternatives:

| Option | Verdict |
|---|---|
| **Declared front-matter** (chosen) | Join key is explicit data, greppable, **validated at build**. Folder names stay cartographer-friendly; machine identity is separate. Carries `kind` + asset targeting — needed by `renders` anyway. |
| Rename dirs to item ids | Overloads the folder name with two jobs; silently breaks if the warehouse changes id minting — coupling with no enforcement. |
| Warehouse-side resolver map | A hand-kept lookup in a third place that drifts. The exact thing we're removing. |

The warehouse treats item ids it doesn't recognize as harmless, and items with no manifest entry
emit unchanged — the manifest and the catalog stay loosely coupled.

---

## 4. Render kind by asset type

Style binds to a renderable asset, so the `renders` shape depends on what that asset is.

| Item asset | `kind` | `renders.<id>` carries | Rendered by |
|---|---|---|---|
| `pmtiles` (vector) | `vector` | `style_url` → GL fragment; paint on `source-layer` | MapLibre (client) |
| `cog` single-band (e.g. gravity raster) | `raster` | `colormap_name` / `colormap`, `rescale`, `nodata` (standard render ext) | titiler (future) |
| `cog` RGB geologic **plate** (pubs) | — | nothing — the plate **is** the cartography, already baked | served as-is |

Consequences:

- **Pubs COGs mostly need no style.** A harvested geologic-map plate is a pre-rendered RGB COG;
  the cartography is in the pixels. No `renders` entry needed (a trivial `default` naming the `cog`
  asset is optional).
- **`units.pmtiles` is the one pubs layer that needs a real vector style** (seamless units, paint
  keyed on `unit_symbol`). It is a vector item, so it falls under row 1 with everything else — no
  special-casing.
- **Single-band scientific rasters** (gravity, heat flow) use the standard render-extension fields
  (`colormap_name` + `rescale`); no `style_url`. These light up once titiler lands (roadmap Tier 2).

### Note on `style_url`

The standard render extension covers raster (`rescale`/`colormap_name`) but has **no field for a
vector GL style** — `style_url` is a local extension (already used by `ugs-styles`). To stay
conventional and discoverable we *also* emit a STAC asset:

```json
"assets": {
  "style": { "href": "https://styles-cdn/.../hazards_qfaults/default.json",
             "type": "application/json", "roles": ["style"],
             "title": "MapLibre GL style (default render)" }
}
```

so a client that walks assets (not `renders`) still finds it. `renders.<id>.style_url` and the
`style`-role asset point at the same URL.

---

## 5. Warehouse changes

- **`core/config.py`** — add `STYLES_INDEX_URL` (CDN URL of `ugs-styles/dist-json/index.json`) and
  `STYLES_CDN_BASE` (style fragment base; may be a different bucket than the warehouse — it's just
  an href). Both env-overridable.
- **`core/styles.py`** (new) — fetch + cache `index.json` once per run; `renders_for(item_id, assets)
  → (renders_dict, style_asset | None)`. Pure/​testable; network isolated to one `fetch`. Graceful:
  unreachable manifest → empty result → items emit as today.
- **`core/stac.py`** — add the render-extension URL constant and a small `attach_renders(item)` helper
  (mirrors the existing `attach_iso`), so all three producers share one code path.
- **Wire into the three emit sites:** `vector/sink_stac.py`, `pubs/sink_stac.py`, `raster/sink_stac.py`
  — call `attach_renders` before `write_item`, passing the item's asset keys.
- **Tests** — `tests/test_styles.py`: manifest lookup, the three kinds, graceful no-match. Mock the
  manifest; no network.

## 6. ugs-styles changes

- Add the `binding` export convention to style modules (§3); document in its `CLAUDE.md`.
- `build-json.ts`: read + **validate** `binding`, emit the richer `index.json` (item id keyed,
  `kind` + `assets` included).
- Re-key existing styles to warehouse item ids (the `_current`-stem ids the warehouse mints).

## 7. Viewer changes (both viewers)

- Read `item.renders` (prefer `default`, else first). Fetch `style_url` → `{ layers }`.
- Replace the flat `colorFor` block (`viewer/src/Map.tsx:109-118`) with: add the PMTiles source,
  then `addLayer({ ...fragment, source, "source-layer": pmLayer })` for each fragment.
- **Legend** falls out of the style for free: walk the paint expression (`match`/`step` on
  `unit_symbol`) → swatches. (Roadmap couples legend with symbology.)
- Keep `colorFor` only as the fallback when an item has no `renders` (un-styled layers stay visible).

---

## 8. Open questions

- **Styles CDN bucket** — `ugs-styles` examples use `ut-dnr-ugs-ucrc-public`; the warehouse bucket is
  `ut-dnr-ugs-maps-prod-public`. Cross-bucket href is fine (it's a URL); decide whether to keep
  styles in their own bucket or rsync into the warehouse bucket. Leaning: keep separate, `ugs-styles`
  owns its CDN.
- **Manifest freshness** — warehouse fetches `index.json` at ingest. A style added after a layer's
  last ingest won't appear until the next ingest or a `refresh_stac` run. Acceptable (matches the
  derive-from-truth catalog refresh); document it.
- **Multiple renders per item** — app-specific variants (e.g. geohaz's 4-way displacement split) are
  additional render ids under the same item id, or a client-side `filter` append (the `ugs-styles`
  README already does the latter). Item id stays the identity either way.
- **source-layer name** — the GL fragment needs the PMTiles `source-layer`; today that's `topic.stem`.
  Confirm it's discoverable from the STAC item (the existing `pmtiles:layers` web-map-link carries it).

---

## 9. First step

Per decision: **this doc first**, reviewed across `ugs-warehouse` + `ugs-styles`, before code. Then the
likely order: warehouse `core/styles.py` + wiring (pipeline half, testable in isolation) → `ugs-styles`
binding + manifest → swap this `viewer/` → `ugs-map-viewer`. Prove the full loop on one vector topic
(`hazards_qfaults`) before scaling to all layers.
