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
4. **Raster *conversion* ownership: open/deferred** — warehouse converts now (`pubs/harvest`,
   interim, bandwidth-driven); may later move to ugs-ingest (#169 batch-feeds published maps),
   or #169 may merge into this repo. Cheap to flip either way because conversion (`harvest.py`,
   swappable) is kept separate from serving (STAC/PMTiles/footprints, stable contract).

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
  vector/                  ← producer A (the day-old vector pipeline, restructured onto core)
    source.py transform.py sink_ducklake.py sink_archive.py sink_pmtiles.py
    ducklake.py topics.py ingest.py
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

0. **Extract the shared core + restructure vector onto it.** Create `core/{config,gcs,stac}.py`
   (config = bucket/CDN/prefixes; gcs = obstore upload/list + Cache-Control + CDN href; stac = item +
   collection builders + web-map-links + titles + the derive-from-truth catalog refresh). Move the
   vector modules into `vector/` and refactor their sinks to call `core/`. The repo is a day old and
   unused, so restructure freely. Verify: `ruff` + offline STAC tests pass; a vector `--dry-run`
   works; update `service/`, `scripts/`, `pyproject` packages, `cloudbuild.yaml` command paths.
1. **Pubs harvest core** — `pubs/identity.py` + `pubs/source.py` (manifest from CSV + footprints
   FeatureServer) + `pubs/harvest.py` (zip→COG→validate). Prove one map → COG locally.
2. **Pubs footprints + vectors** — `pubs/footprints.py` (GeoParquet + PMTiles), `pubs/vectors.py`
   (GIS → GeoParquet), units PMTiles.
3. **Pubs STAC via the core** — pub items (+ cloud-optimized COG asset, `ugs:topic`, titles) emitted
   through `core/stac.py` into the `ugs-publications` collection of the one catalog.
4. **Packaging + deploy** — `Dockerfile.harvest`, pubs deps as a `pyproject` extra, second Cloud Run
   Job in `cloudbuild.yaml` + `DEPLOY.md`. **Ship only after all phases pass.**

Phase 0 is the streamlining work and de-risks everything after it; it touches the working pipeline,
so it goes first and must leave the vector artifacts byte-for-byte equivalent.

## Open notes

- COG compression: webp q90 (geolmap default) reads only on webp-enabled GDAL; the harvest image has
  it. Keep that default; document it.
- `PUBS_DB_URL` (MySQL) is optional — snapshot CSVs are the default source of truth.
- The `ugs:topic` classifier applies to pub items too. A pub's COG is drawn from its cloud-optimized
  `cog` asset (media type `…;profile=cloud-optimized`) — STAC Browser/viewer render it natively; no
  web-map-links `cog` link (that extension defines no `cog` rel).
- Thumbnails (`build_thumbnails.py`) optional — bring if STAC items want a `thumbnail` asset.
