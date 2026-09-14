# Design: Load the geomap geologic-map corpus into the warehouse — faithful, per-publication, versioned

- **Jira:** ALL-5911 (Task, under epic ALL-3865 "Create Data Ingestion Pipeline")
- **Subtasks:** ALL-5912 (versioning/editions), ALL-5913 (expose vectors + companion tables), ALL-5914 (ingest the missing maps), ALL-5922 (scale-tier layers as first-class grouped items)
- **Related:** ALL-5837 (land-raw re-architecture), ALL-5832, ALL-5831, ALL-5480, ALL-5860, ALL-5826, ALL-5825, ALL-5827
- **Date:** 2026-09-13
- **Status:** Draft for review
- **Scope:** Sub-project **A** (publications ingest) of the geomap → warehouse migration. The viewer (**B**) and feature-access/cutover (**C**) are separate specs and explicitly out of scope here.

---

## TL;DR

Finish landing the geologic-map corpus that `geomap.geology.utah.gov` (the `geolMapPortal` app) serves into the ugs-warehouse as **immutable, per-publication artifacts with the published originals preserved byte-for-byte**, so the map endpoints can later repoint off ArcGIS Server / AGOL / pg_featureserv. Verified 2026-09-13, **~98% of the rasters are already loaded**; this work closes the remaining gaps, surfaces already-extracted data as STAC assets, and adds the one genuinely-missing piece — a per-publication versioning/preservation model. We **extend the production `pubs` producer** and lift proven pieces from the `ugs-geolmap-cog-poc`; we do not stand up a second producer.

---

## 1. Background — verified current state

### 1.1 What geomap is and consumes
`geomap.geology.utah.gov` = the `geolMapPortal` app (ArcGIS JS 4.29, deployed `ut-dnr-ugs-geolmapportal-prod.web.app`). It is a **SceneView 3D-drape** app by default (2D `MapView` via `?view=map`), draping scale-tiered geologic rasters over Esri world elevation at 2.5× exaggeration. It reads:

- **Raster map face** — `webmaps.geology.utah.gov/arcgis/rest/services/GeolMap/*` (ArcGIS Server 10.91): MapServers `500k_State` / `30x60_Quads` / `7_5_Quads` / `2500k_Nationwide` + ImageServers `MD_24K` / `MD_100K`, switched by scale.
- **Feature identify** — pg_featureserv `postgisftw.unit_desc_sym_age_by_point_scale` (scale = small/intermediate/large) over a **harmonized `seamlessgeolunits` compilation**, plus unit name/age autocomplete.
- **Footprints / downloads** — AGOL `Geologic_Map_Footprints_View` (fields `series_id`, `scale`, `quad_name`, `geomaps_service`).

So "COG draped over terrain with feature access" is not new — it is a faithful re-creation of what geomap already does, but warehouse-backed. That re-creation is sub-project B/C; **this spec only lands the data.**

### 1.2 Coverage (measured 2026-09-13, public AGOL + STAC CDN)
- geomap serves **1,046 unique maps** (1,050 footprint rows; 838 at 1:24k).
- warehouse holds **3,102 publication items** (the full pubs catalog, not just maps).
- **P ∩ W ≈ 1,026–1,028 (~98%)** of served maps already loaded; per-pub COG under `ugs-publications/{SERIES}`, scale mosaics under `ugs-geologic-maps`.
- **P \ W = 18–20 genuine gaps** (see §6).
- Of loaded maps (30-item sample): **~87% carry a COG, only ~10% have vector GeoParquet**, ~17% have a gdb/shp source zip. The vector scarcity is a **source-data reality** — ~90% of historical maps were never digitized — not a warehouse gap.

**Implication for the overall migration:** the raster load is essentially done. Faithful per-publication feature access is only possible for the ~10% with vector; the statewide identify UX depends on the harmonized `seamlessgeolunits` mart, which is a **derivative built after load → group → version** (deferred to sub-project C, out of scope here).

### 1.3 Item-model state in the production `pubs` producer (verified in code)
`src/ugs_warehouse/pubs/` (ingest.py, source.py, harvest.py, vectors.py, sink_stac.py, mirror.py, identity.py, geolmap_mosaics.py) + `src/ugs_warehouse/core/` (stac.py, gcs.py, config.py).

| Capability | State | Evidence |
|---|---|---|
| Curatable metadata | **Done** | `override > source > prior` (preserve-on-empty); `warehouse/overrides/{item_id}.json` (config.py, ingest.py, sink_stac.py) |
| COG raster load | **~98%** | per-pub COG warped to EPSG:3857, web-optimized; 3 fixed-tier mosaics (geolmap_mosaics.py bins scale into 24k/250k/500k) |
| Immutable data assets | **Partial** | immutable `Cache-Control` (core/gcs.py `CACHE_IMMUTABLE`) + `SKIP_EXISTING`; **not** content-addressed; `--force` overwrites |
| Original source-bundle archival | **Partial** | mirror.py path-preserves + immutable-caches PDFs/plate/GIS zips, but **selective** (COG-bearing pubs only) and **not linked as a STAC asset** (see ALL-5832) |
| All vector layers | **Extracted, not exposed** | vectors.py extracts **every** spatial layer to `VECTORS_PREFIX/{series_id}/{layer}.parquet` (CRS/fields as-is), but sink_stac.py surfaces only a single `units` asset |
| Companion non-spatial tables | **Missing** | vectors.py `if geomtype:` skips DescriptionOfMapUnits / CorrelationOfMapUnits |
| Versioning / editions | **Missing** | core/stac.py `write_item` overwrites; no predecessor/successor rels; supersession only free-text in the pubsdb CSV |
| Grouping / dataset model | **Partial** | collection-nesting by series code only; no whole/members, no edition families |
| Scale-tier layer ↔ member pubs | **Missing** | mosaic items carry a bare `ugs:map_count`; no `rel:"related"` either direction; pubs don't reference their tier (geolmap_mosaics.py, pubs/sink_stac.py) |

### 1.4 Two implementations
The standalone `ugs-geolmap-cog-poc` already has, and the production producer lacks or under-uses: robust multi-layer GeMS extraction with subprocess isolation + per-layer schema log (`pipeline/extract_vectors.py`); flexible scale-banded raster PMTiles plates (`pipeline/build_pmtiles.py`, `denom()` + `--scale-filter`); harvest-from-`ugspub` via `data.php` with GeoTIFF-Zip vs GIS-Data-Zip precedence and PDF rasterization (`pipeline/harvest.py`). **We lift these into the production producer** rather than run the POC as a second producer — one producer, one catalog, shared core.

---

## 2. Goal & non-goals

**Goal:** every geologic map geomap serves is present in the warehouse as a per-publication STAC item that (a) archives the published originals immutably, (b) exposes all already-derived data as assets, (c) preserves each publication's raw schema unaltered, and (d) is versioned so published data is never mutated and editions are retained.

**Non-goals (explicit):**
- The viewer / 3D terrain-drape / MapLibre-vs-ArcGIS decision (sub-project B).
- Warehouse equivalents for the `postgisftw.*` identify functions and the ArcGIS/AGOL/pg_featureserv cutover (sub-project C).
- The harmonized `seamlessgeolunits` mart — a deferred derivative built after load → group → version (sub-project C).
- Digitizing the ~90% of maps that have no vector source. Out of scope; we preserve them faithfully as raster + PDF.

---

## 3. Design principles (locked with the requester)

1. **One producer, one catalog.** Extend the production `pubs` producer; lift the POC's multi-layer extraction and flexible scale-banding. Shares ALL-5837's land-raw philosophy on a different (file-harvest) pipeline.
2. **Immutable data, curatable metadata, additive editions.** Published DATA assets are content-addressed and never overwritten; descriptive METADATA stays editable in place; a revised map is a new item with `predecessor-version`/`successor-version` links, all editions retained.
3. **The archived originals are the preservation guarantee.** Keep the original COG and the original GIS bundle (gdb/shp) as first-class assets, byte-for-byte. Everything cloud-native is a rebuildable derivative that never overwrites an original.
4. **Preserve raw schemas verbatim + an additive common projection.** Each vector layer lands as GeoParquet with original column names/types untouched; a thin standard identify projection (unit symbol/name/age/description) is computed additively, never overwriting.
5. **Scale-tiering is data, not a viewer trick.** All three scale tiers are publications; the serving mosaics are scale-banded; per-publication identify (where vector exists) resolves against the covering publication's own vector.
6. **Fail loud, coverage honesty.** Per-publication `errors[]`/`warnings[]`; no publication silently dropped; PDF-only and no-vector maps are landed honestly (no fabricated raster/vector).

---

## 4. Data model

### 4.1 Per-publication item
One STAC item per publication under `ugs-publications/{SERIES}` (item id = sanitized `series_id`), as today. The `ugs-geologic-maps` collection holds the derived scale-tier serving mosaics (see §4.5), **not** the per-publication items. Whole/members linkage between the two is in scope here (§4.5); the deeper catalog-grain redesign — whether the map corpus becomes its own grouped collection, layer-as-item vs layer-as-collection — remains the larger open question ALL-5829.

### 4.2 Asset taxonomy
- **Originals (immutable, content-addressed):** `cog` (published raster), `source_bundle` (original gdb/shp zip — new first-class asset; see ALL-5832).
- **Derivatives (rebuildable):** one GeoParquet asset **per vector feature class** (contacts, faults, points, orientations, units, …), one per **companion table** (DescriptionOfMapUnits, CorrelationOfMapUnits), scale-banded raster PMTiles plates (statewide, in `ugs-geologic-maps`), thumbnails/preview.
- **Metadata:** ISO 19139 sidecar, PDF `publication`, curated fields.

### 4.3 Preservation model
Originals are the source of truth. Derivatives are generated from originals and can be rebuilt without touching them. Raw vector schemas are preserved verbatim; the additive common projection (standard identify fields) is stored as extra columns or a side table, never by rewriting the raw layer.

### 4.4 Versioning / editions (the new build — ALL-5912)
- **Content-address published data assets** (hash or version segment in the GCS object path) so a reingest writes a new object rather than overwriting; harden the `--force` path so it cannot silently mutate a published asset.
- **Curatable metadata** stays as today (`override > source > prior`).
- **Editions:** a revised map is a new item; link with the STAC **Versioning Indicators** extension (`https://stac-extensions.github.io/version/v1.2.0/schema.json` — `version`/`deprecated` fields + `predecessor-version`/`successor-version`/`latest-version` rels). Detection: distinct `series_id` values coexist naturally (the common case); a re-harvest of the **same** `series_id` with changed content mints a new version rather than overwriting the published one. Supersession currently living as free text in the pubsdb CSV is promoted to these machine-readable links where known.

### 4.5 Scale-tier layers & grouping — whole/members (ALL-5922)

geomap organizes its services **by scale-tier as a layer that spans publications** (24k / 100k / 500k / statewide). We preserve that in the warehouse: each scale-tier serving layer is loadable on its own **and** cross-linked with its member publications, both directions.

- **Already satisfied — scale-tier layers are first-class loadable items.** The `ugs-geologic-maps` mosaics (`geologic-maps-24k/250k/500k`) load through the viewer's add-to-map path via `rasterPmHref`, detected by a `pmtiles` asset with `roles:["visual"]` / `ugs:render:"raster"` — the same path as any vector/COG layer (`viewer/src/map/map-model.ts`, `app.tsx`, `stac.ts`). No viewer change is needed for "load the scale-tier layer on its own."
- **Net-new — whole/members linkage.** Verified: a mosaic item carries only a bare `ugs:map_count`, and publications don't reference their tier. We add the linkage with the **warehouse-idiomatic `rel:"related"` mechanism** (the item↔item graph already used in `vector/related.py`) — *not* a new construct:
  - mosaic (scale-tier) item → one `rel:"related"` link per member publication (membership is already computed at build time by `geolmap_mosaics.tier_of()`); keep `ugs:map_count` as the rollup;
  - each publication item → a `rel:"related"` link + a `ugs:scale_tier` property to the scale-tier layer(s) it contributes to.
  - Result: load a scale-tier layer directly (works today), reach a publication's scale-tier layer from the publication, and enumerate a scale-tier layer's members.

**Grouping axes:**
- **By scale** — primary, matching geomap: the mosaic items + `ugs:scale`, membership materialized as above.
- **By topic** — reuse the existing `ugs:topic` classifier (`pubs/topic.py`, already stamped on publication items); extend it to the mosaic/serving items and surface it as a real item property so maps group/filter by topic. The viewer's category taxonomy (`viewer/src/catalog/item-view.ts`) already folds the `mapping` schema + `ugs-geologic-maps` into one "Geologic Maps" facet — we make that server-driven rather than a hardcoded client list.

**Deliberately not invented:** there is no formal dataset/groups model in ugs-warehouse (the canonical dataset+grouping model lives on the dataELT side, not this repo). We use the existing `rel:"related"` + collection-nesting + `ugs:topic` idioms. A materialized "whole → member items" list is net-new but follows the `related`-link fan-out precedent in `vector/related.py`.

### 4.6 STAC conformance

Every construct here is STAC-spec-legal — a declared extension or a spec-permitted mechanism, no bespoke schema:

- **Editions:** STAC **Versioning Indicators** extension (`https://stac-extensions.github.io/version/v1.2.0/schema.json`) — `version` / `deprecated` fields + `predecessor-version` / `successor-version` / `latest-version` rels.
- **Relationships / membership:** `rel:"related"` — IANA-registered, which the STAC best-practices permit; already the warehouse's item↔item idiom (`vector/related.py`).
- **Raster CRS:** Projection extension (`proj:code`), as COGs already use.
- **Custom fields:** prefixed `ugs:*` properties (`ugs:scale_tier`, `ugs:topic`, `ugs:scale`, `ugs:map_count`) — spec-legal prefixed fields (as `ugs:foreign_keys` already is); declared via a `ugs` extension schema where one is warranted.
- **Assets, tables, alternates:** existing declared extensions (`web-map-links`, `table`, `alternate-assets`, `file`, `classification`).

---

## 5. Components & code changes

- **`pubs/vectors.py`** — remove/relax the `if geomtype:` filter to also capture companion non-spatial tables; keep full-schema/CRS preservation; adopt the POC's subprocess isolation + per-layer schema log (ALL-5913).
- **`pubs/sink_stac.py`** — attach every extracted vector layer and companion table as its own STAC asset (today only `units`); add the `source_bundle` asset; add version links (ALL-5912, ALL-5913, ALL-5832); emit a `rel:"related"` link + `ugs:scale_tier` to the scale-tier layer and ensure `ugs:topic` is stamped on map items (ALL-5922).
- **`pubs/mirror.py` / `identity.py`** — make original-bundle archival universal (not COG-bearing-only) and content-addressed (ALL-5912, ALL-5832).
- **`pubs/geolmap_mosaics.py`** — emit a `rel:"related"` link from each scale-tier mosaic to every member publication (membership already known via `tier_of()`); keep `ugs:map_count` as the rollup (ALL-5922). Optionally lift the POC's `denom()` + `--scale-filter` flexible banding if the 3 fixed tiers prove too coarse.
- **`pubs/topic.py`** — extend the `ugs:topic` classifier to mosaic/serving items so the by-topic grouping axis is server-driven (ALL-5922).
- **`core/stac.py` / `core/gcs.py`** — write-once semantics / content-addressed paths for data assets (ALL-5912).
- **Missing-map ingest** — run the producer for the 18–20 gaps; land source-less series as download-only items (ALL-5914).

---

## 6. Coverage gaps to close (ALL-5914)

Served maps not yet loaded (verified; loose-match recovery found none of these elsewhere in the warehouse):
`OFR-781DM`, `OFR-782DM`, `OFR-783DM`, `OFR-784DM` (780 is loaded); `FSM-18`, `FSM-20`, `FSM-22`; `BYU_GS-V19_MILLFORK`, `BYU_GS-V27_BIGHOLLOW`, `BYU_GS-V27_SMP`, `BYU_GS-V32_FILLMORE`, `BYU_GS-V32_KANOSH`, `BYU_GS-V5-IS`, `BYU_SA-6564_VERNAL_NW`; `AAPG_B-V43`; `USU-MS-628`; `UU-60-GC`; `UU-MS-601`.

Also in scope for the corpus's data quality (tracked as linked siblings, not duplicated here): footprints/no-geometry (ALL-5831), stale vendored CSV source (ALL-5480), curator metadata fields (ALL-5860), publisher-scan-as-primary-raster provenance (ALL-5826), null datetimes (ALL-5827), publish/prune/refresh (ALL-5825).

---

## 7. Error handling

Fail loud per house rules. Harvest/convert/extract failures are collected **per publication** into `errors[]`/`warnings[]` and surfaced; a poison source (bad SRS, corrupt gdb) is isolated (POC subprocess pattern) so one map cannot abort the batch, and it is reported, never silently skipped. Coverage is reported as counts (loaded / missing / source-unavailable), never estimated away.

---

## 8. Testing

- Unit: companion-table extraction; per-layer asset attachment; content-addressed path generation; version-link emission; write-once guard rejects an overwrite of a published data asset.
- Integration: ingest a representative GeMS pub (all feature classes + DMU/CMU → assets, raw schema intact); reingest unchanged (no-op); reingest changed (new version, original untouched); a PDF-only pub (download-only item, no fabricated COG/vector).
- Reconciliation: re-run the portal-vs-warehouse coverage check; assert the 18–20 gaps are closed or documented source-unavailable.

---

## 9. Open decisions (to resolve in the implementation plan)

- Content-addressing scheme for data assets (hash in path vs version segment) and how it interacts with the existing path-preserved mirror layout.
- Whether flexible scale-banding (POC) is needed now or the 3 fixed tiers suffice.
- Exact shape of the additive common identify projection (extra columns vs side table) — coordinate with sub-project C so it feeds the eventual identify without pre-empting the mart design.
- Scale-tier membership: materialize it in STAC via `rel:"related"` (chosen — warehouse-idiomatic) vs declaring it upstream in `raw.schema_registry.relationships` and projecting it (as vector relationships are). For pubs the tier is derived at mosaic-build time, so materializing directly is simplest — confirm this doesn't diverge from the vector-side relationship source of truth.
- By-topic grouping — **decided:** a lightweight `ugs:topic` property on map/mosaic items in A (spec-legal prefixed field); topic *collections* and the viewer facet (including retiring the hardcoded `item-view.ts` fold once `ugs:topic` is server-authored) are deferred to sub-project B.

---

## 10. References

- Verified findings + decisions: memory `geomap-warehouse-migration`.
- ALL-5837 land-raw re-architecture (sibling philosophy).
- `ugs-geolmap-cog-poc` (pieces to lift: `pipeline/extract_vectors.py`, `pipeline/build_pmtiles.py`, `pipeline/harvest.py`).
- Production producer: `src/ugs_warehouse/pubs/`, `src/ugs_warehouse/core/`.
