# 3D publication pipeline — plan (not yet built)

**Status:** design only. Gated on wiring a **3D ingest entry point** (a job/CLI that takes a pub's
geodatabase and produces warehouse assets). The conversions below are *outputs of that entry point* —
we don't convert anything until the entry point exists. Repo-only doc (infra).

Pilot pub: **OFR-778DM** (Woodland quad) — UGS's first GeMS 3D cross-section publication. Its 3D data
currently exists only because a hardcoded one-off was hand-run (since deleted); there is no repeatable
ingest yet.

## Principle

**Arc assets stay as the download/archival truth** (`geodatabase_zip`, `arcgis_pro_3d_mapx_scene`) —
they're fine as-is. Cloud-native formats are *added derivatives*, served to the viewer.

## Inputs (per 3D pub)

- The geodatabase: `CSA_3D_MapUnitPolys`, `CSA_3D_ContactsAndFaults`, `DescriptionOfMapUnits` (GeMS).
- The existing map-sheet COG (already produced by the pubs pipeline).

## Outputs (new STAC assets; Arc assets retained)

| Asset | Format | Purpose | Roles |
|---|---|---|---|
| `fence_polygons` | **3D GeoParquet** (WKB-Z, EPSG:4326) | served — viewer reads via duckdb-wasm | `data`, `3d-vector` |
| `fence_lines` | 3D GeoParquet | contacts / faults | `data`, `3d-vector` |
| `fence_mesh` | **glTF / GLB** | download + interop (Blender, ArcGIS Pro, web 3D) | `data`, `visual` |

The GeoParquet carries the geometry + `MapUnit` + `label`; **colors ride the standard STAC
Classification extension** (see below), not a per-row column.

⚠️ **Color source + the built-in mechanism (verified on OFR-778DM):**
- `DescriptionOfMapUnits.AreaFillRGB` is **empty** — authored colors live in the **ArcGIS symbology**
  (the `.mapx`), in the **`CSA_3D_MapUnitPolys` layer's `CIMUniqueValueRenderer`**, keyed on the
  `Symbol` field (`"24_Tk_…"` → MapUnit `Tk`). All 17 fence units are present and authoritative (it's
  the cross-section's own symbology). DMU supplies `Name` + `HierarchyKey` (label + stratigraphic order).
- ⚠️ **Colors come in multiple CIM color types** — most are `CIMRGBColor`, but some (e.g. `Qay`, `TRt`)
  are **`CIMCMYKColor`** (and possibly Gray/HSV). The parser must convert all of them; an RGB-only walk
  silently drops those units. (Earlier mistake: dropped Qay/TRt, then COG-sampled them — both samples
  were wrong; the authored Qay is pale yellow, TRt green.) **No COG sampling needed.**
- **Serve the colors as `classification:classes`** — the warehouse's existing standard (vector topics
  already emit it via `core/styles.classification_classes` + `attach_classification`; the viewer reads
  it via `classificationEntries` / `classificationColors`). The 3D pipeline stamps the same property:
  one class per MapUnit `{value, name, title, color_hint}`. **No bespoke sidecar** — the viewer already
  consumes this. (The current `viewer/public/3d-colors/<id>.json` is interim until a reingest stamps
  `classification:classes`; the viewer prefers the standard property when present.)
- Proven locally: `pyogrio` (GDAL-bundled wheel, no system GDAL) reads the GDB; a parser walks the
  `.mapx` JSON for the colors.

## Why GeoParquet (not the current GeoJSON)

Today the fence is served as GeoJSON (`*_3d_polygons.geojson`) — plain text, uncompressed, no range
requests, parsed whole client-side. GeoParquet is columnar, compressed, range-requestable, matches the
warehouse's existing vector-archive format, and the viewer **already runs duckdb-wasm** (search + data
explorer), so it can read the fence straight from Parquet.

## Steps

1. Read GDB layers (pyogrio / GDAL `OpenFileGDB`).
2. Parse the `.mapx` `CIMUniqueValueRenderer` → `MapUnit → fill_rgb` (Symbol-keyed, see above).
   `DescriptionOfMapUnits` → `MapUnit → {Name, HierarchyKey}` (label + order).
3. Reproject to 4326, **keep Z**.
4. Join colors + DMU to features (`fill_rgb`, `label`, `hierarchy_key`).
5. Write GeoParquet (polygons, lines), WKB-Z geometry.
6. Triangulate fence panels → glTF/GLB.
7. Write STAC assets + ISO sidecar.

### Cartography edge cases (both handled in the viewer; the pipeline must too)

- **All units are in the cross-section renderer** — including surficial (`Qay`, `TRt`). The trap is the
  **color encoding** (RGB vs CMYK, see above), not missing units. Parse every CIM color type.
- **Cross-section colors may differ from the map plate (COG).** The fence is authored with the
  cross-section's own symbology; that's authoritative for the fence even where it diverges from the
  plan-view plate. (Don't "fix" the fence to match the COG — they're different authored products.)
- **Line cartography** comes from the GeMS `Type` + `Symbol` fields: contact / fault / section-boundary,
  and `Symbol` carrying "approximately located" (→ **dashed**) vs "well located" (→ solid). All black
  (heavier for faults). Reverse-fault teeth not yet done.

## Viewer change

Swap the GeoJSON `fetch` + manual parse → duckdb-wasm read of the GeoParquet; use the `fill_rgb`
column for fence colors (delete the invented `GEOLOGIC_COLORS` map + hash fallback in `Browse.tsx`);
order the legend by `hierarchy_key` (stratigraphic) instead of alphabetical.

## Terrain — already solved, not part of this

The 3D terrain surface is sampled **live from USGS 3DEP** (`terrain.ts`, `getSamples`, CORS-open,
public domain, 1 m lidar over Utah). No hosting, no pipeline — any pub's bbox works automatically. The
old `build_terrain_rgb.py` (DEM tile hosting) is obsolete and removed.

## Phasing

1. **GeoParquet-3D + authored colors** → viewer reads it. *(cloud-native + cartography — the payoff)*
2. **glTF export** → download/interop.
3. **Generalize**: a `ugs-pubs-threed` Cloud Run job + admin-console button; auto-discovers `CSA_3D`
   layers on any pub. (Mirrors the other pubs jobs.)

## Verified empirically (locally, against the real OFR-778DM GDB)

- ✅ **`pubs/threed.py` built** — GDB + `.mapx` → 3D GeoParquet (polys 37, lines 48) +
  `classification:classes`. CRS 4326, **Z preserved** (`has_z`), valid GeoParquet `geo` metadata
  (cloud-native), **all 17 units coloured** (RGB+CMYK), lines classified (contact/fault/boundary +
  dashed-for-approximate).
- ✅ **duckdb reads Z from the GeoParquet** — duckdb 1.5.3 (the viewer's duckdb-wasm family) hydrates
  it as native `GEOMETRY`; `ST_AsWKB` type code `1006` (ISO WKB-Z), `ST_AsGeoJSON` returns 3-coord
  vertices.
- ✅ **Viewer reads the GeoParquet in-browser** — `download.readFeatures3D` reads the whole file via
  duckdb-wasm, geometry as WKB BLOB parsed by `wkb.wkbToGeoJSON(blob, keepZ=true)` (no spatial
  extension — it trips duckdb-wasm). The 3D viewer is now dual-source: GeoParquet (a `.parquet`
  3d-vector asset, or same-origin override) → GeoJSON fallback. Colour precedence: parquet `fill`
  column → `classification:classes` → sidecar → placeholder. Verified end-to-end: fence renders from
  parquet, Z intact, authored fills, and the legend gains full formation names (the DMU `Name` the
  conversion joined). `wkbToGeoJSON` gained a `keepZ` flag (was 2D-only).

## Still to verify

- glTF triangulation of vertical fence panels — orientation / winding correct (phase 2).

## Work-box image deps to add (Dockerfile)

Not currently installed: **GDAL / pyogrio / geopandas** (GDB read) and **pygltflib / trimesh** (glTF).
Add to the harvest/pubs runtime image as part of this work.

## Source note

The GDB download is reachable on our CDN
(`maps-assets.geology.utah.gov/publications/OFR/OFR-778DM/OFR-778DM_Woodland_gdb.zip`) even though the
STAC `geodatabase_zip` href is broken (a doubled-path `ugspub.nr.utah.gov/.../publications/publications/…`
that 404s — fix the href construction in the same pass).
