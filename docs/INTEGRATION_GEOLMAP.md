# Integration plan — geolmap COG/publications pipeline → warehouse

Merge the **processing + artifact-generation core** of `ugs-geolmap-cog-poc` into this
repo as a second pipeline. Drop the POC visualization scaffolding. **No deploy until the
whole integration is done** — build all phases, then ship.

## Scope

**Bring in (the core):**
- harvest: pub zip → PDF/GeoTIFF → `gdalwarp` clip+reproject(3857) → `rio-cogeo` COG → validate
- inventory/manifest, footprints (GeoParquet + PMTiles), vector/unit extraction (GeoParquet),
  units PMTiles
- pub STAC items (`build_pubs_stac`) + the `ugs:topic` classifier + pubs metadata source
- the vendored `data/*.csv` pubs snapshot

**Drop (POC viz / scaffolding):**
- `app/` (React viewer), `serve/` (local server), `orchestration/local` Airflow demo,
  `deploy/` nginx, `build_viewer_index.py`, `build_static_catalog.py` (pystac tree — the
  warehouse has its own catalog), geolmap's `AGENTS.md` / POC docs, the thermal-fleet notes.

## Locked decisions

1. **Module name:** `pubs/` (publication-centric, broader than "geolmap").
2. **Pubs metadata:** vendored `data/*.csv` snapshot now; `PUBS_DB_URL` (live MySQL) env-optional later.
3. **Separate harvest image + Cloud Run Job** (`Dockerfile.harvest`, job `geolmap-harvest`) — GDAL +
   poppler + rio-cogeo are too heavy for the slim vector-ingest image. Two images, two jobs.

## Architecture principle — shared core, pluggable producers

Both pipelines reduce to the **same** end pattern: *produce cloud-native assets on GCS +
emit a STAC item → aggregate into one catalog*. Only the **source** and **transform**
differ (Postgres+DuckDB vector vs pub-zip+GDAL raster). So instead of two stacks that each
reinvent STAC/GCS/catalog, we extract a **shared core** that both producers call:

```
            ┌────────────── shared core (ugs_warehouse.core) ──────────────┐
  source ─► │  stac (item/collection builders, web-map-links, titles)      │
  transform │  catalog (unified refresh: collections, derive-from-truth)    │ ─► GCS + one
            │  gcs (obstore upload/list, CDN hrefs, Cache-Control)          │    STAC catalog
            │  config (bucket/CDN/prefix env)                               │
            └───────────────────────────────────────────────────────────────┘
   producer A: vector topics (DuckDB)      producer B: pubs/COG (GDAL)
```

The existing vector sinks (`sink_archive/pmtiles/stac`) get refactored to call the shared
core (low-risk, mostly moving STAC/GCS code that already exists). The pubs producer is new
and uses the same core — so it never reinvents STAC items, the catalog, or GCS upload. This
is the streamlining: one STAC implementation, one catalog, one GCS layer, two producers.

The vector contract (`source.read()→pyarrow→transform`) stays for producer A. Producer B
(per-`series_id`, GDAL) has its own source/transform — they converge only at the core.

## Target structure

```
src/ugs_warehouse/
  core/                    ← shared, used by BOTH producers
    stac.py                STAC item + collection builders, web-map-links, titles, CDN hrefs
    catalog.py             unified catalog refresh (collections, derive-from-truth) [was sink_stac bits]
    gcs.py                 obstore upload/list, Cache-Control, public-URL construction
    config.py              bucket / CDN base / prefixes
  source.py transform.py   ← producer A (existing vector pipeline) — stays in place,
  sink_*.py ingest.py        sinks refactored to CALL core/ (no risky file moves)
  topics.py                  (a future cosmetic move into vector/ is optional, not now)
  pubs/                    ← producer B (geolmap core, ported onto the shared core)
    identity.py            Pub(series_id, …) — the Topic analog
    source.py              pubs metadata (CSV/MySQL) + footprints FeatureServer + manifest
    harvest.py             zip → COG (GDAL / rio-cogeo / poppler), validate
    vectors.py             GIS bundle → GeoParquet
    footprints.py          FeatureServer → GeoParquet + PMTiles
    ingest.py              per-series orchestrator (+ --all over the manifest)
  ducklake.py              DuckLake Postgres catalog (vector-only; stays its own concern)
data/                      vendored pubs CSV snapshot
Dockerfile                 slim, vector ingest
Dockerfile.harvest         GDAL/poppler/rio-cogeo base for the pubs pipeline
cloudbuild.yaml            two builds + two job deploys
```

> The vector→core refactor is a **move, not a rewrite** — the STAC/GCS code already exists in
> `sink_stac`/`sink_archive`; we relocate it into `core/` and have the vector sinks call it.
> The working 25-topic pipeline must keep passing at each step.

## Shared-infra integration points

1. **obstore** — geolmap already uses `obstore`/`GCSStore`. Reuse the warehouse upload helpers.
2. **STAC catalog — unify (forces the collections refactor).** Both pipelines write items under one
   `stac/` prefix; `refresh_catalog()` builds a root → **collections** (`ugs-serving-topics`,
   `ugs-publications`) → items. geolmap items already carry `"collection": "ugs-publications"`.
3. **GCS/CDN** — same bucket + CDN (`maps-assets.geology.utah.gov`), prefixes `warehouse/…` +
   `geolmap/…`, same https hrefs + Cache-Control.
4. **Deps/Docker** — `Dockerfile.harvest` on an `osgeo/gdal` base + the pubs requirements; slim image
   unchanged.
5. **Deploy** — a second Cloud Run Job (`geolmap-harvest`) in `cloudbuild.yaml` + `docs/DEPLOY.md`.

## Collections refactor (STAC) — spec

Current catalog is flat (root → items). Change to root → collections → items:
- `refresh_catalog()` groups items by their `collection` field (read from each item, or infer from
  the GCS prefix) and emits a `collection.json` per group + a root linking the collections.
- Vector-topic items gain `"collection": "ugs-serving-topics"`; pub items already have
  `"ugs-publications"`. Optionally sub-collect vector topics by mart schema.
- Keep derive-from-truth (list GCS), keep it idempotent.

## Phases (build order — each builds + keeps the 25-topic pipeline green; none deploys until the end)

0. **Extract the shared core.** Create `core/{stac,catalog,gcs,config}.py` by relocating the existing
   STAC/GCS/CDN/Cache-Control/catalog-refresh code out of `sink_stac`/`sink_archive`/`sink_pmtiles`,
   then have those sinks **call** the core. The vector files stay in place (no risky package move).
   Add the **collections** model + **web-map-links** + **titles** in the core (benefits both
   producers). Verify: `--dry-run` + `ruff` + the offline STAC tests still pass; a vector ingest
   emits identical artifacts.
1. **Pubs harvest core** — `pubs/identity.py` + `pubs/source.py` (manifest from CSV + footprints
   FeatureServer) + `pubs/harvest.py` (zip→COG→validate). Prove one map → COG locally.
2. **Pubs footprints + vectors** — `pubs/footprints.py` (GeoParquet + PMTiles), `pubs/vectors.py`
   (GIS → GeoParquet), units PMTiles.
3. **Pubs STAC via the core** — pub items (+ COG/raster web-map-link, `ugs:topic`, titles) emitted
   through `core/stac.py` into the `ugs-publications` collection of the one catalog.
4. **Packaging + deploy** — `Dockerfile.harvest`, pubs deps as a `pyproject` extra, second Cloud Run
   Job in `cloudbuild.yaml` + `DEPLOY.md`. **Ship only after all phases pass.**

Phase 0 is the streamlining work and de-risks everything after it; it touches the working pipeline,
so it goes first and must leave the vector artifacts byte-for-byte equivalent.

## Open notes

- COG compression: webp q90 (geolmap default) reads only on webp-enabled GDAL; the harvest image has
  it. Keep that default; document it.
- `PUBS_DB_URL` (MySQL) is optional — snapshot CSVs are the default source of truth.
- The `ugs:topic` classifier and web-map-links rendering apply to pub items too (COG via a COG/raster
  web-map-link, not pmtiles).
- Thumbnails (`build_thumbnails.py`) optional — bring if STAC items want a `thumbnail` asset.
