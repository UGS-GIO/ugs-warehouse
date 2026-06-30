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

Each GeoParquet row carries `fill_rgb` (hex), `label`, and `hierarchy_key`. So **cloud-native format
+ authored cartography + a stratigraphically-ordered legend all fall out of one GDB read.**

⚠️ **Color source (verified on OFR-778DM):** `DescriptionOfMapUnits.AreaFillRGB` is **empty** — the
authored colors live in the **ArcGIS symbology** (`.mapx`), as a `CIMUniqueValueRenderer` keyed on the
`Symbol` field (`"24_Tk_Keetley…"` → MapUnit `Tk`), with the RGB in a `CIMRGBColor`. The DMU table
still supplies `Name` + `HierarchyKey` (legend label + stratigraphic order). Proven locally: `pyogrio`
(GDAL-bundled wheel, no system GDAL) reads the GDB; a parser walks the `.mapx` JSON for the colors. An
interim baked sidecar (`viewer/public/3d-colors/<id>.json`) already wires real colors into the viewer;
the pipeline folds this into the GeoParquet's `fill_rgb`.

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

## To verify empirically when building (test, don't assume)

- Does the viewer's **duckdb-wasm read Z geometry from GeoParquet**? (Is spatial loaded? Does it
  hydrate WKB-Z?) If not, decode WKB client-side or carry x/y/z columns instead of WKB.
- glTF triangulation of vertical fence panels — orientation / winding correct.

## Work-box image deps to add (Dockerfile)

Not currently installed: **GDAL / pyogrio / geopandas** (GDB read) and **pygltflib / trimesh** (glTF).
Add to the harvest/pubs runtime image as part of this work.

## Source note

The GDB download is reachable on our CDN
(`maps-assets.geology.utah.gov/publications/OFR/OFR-778DM/OFR-778DM_Woodland_gdb.zip`) even though the
STAC `geodatabase_zip` href is broken (a doubled-path `ugspub.nr.utah.gov/.../publications/publications/…`
that 404s — fix the href construction in the same pass).
