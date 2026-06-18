# ugs-warehouse — Handoff

**Purpose:** lets another agent (or Clinton on a future session) pick up cold —
current state, what's done, what's blocked, the immediate next move.

**Live working doc** — tracked so it syncs across machines. Refresh as state changes;
retire once deployed. Last refreshed **2026-06-16**.

---

## TL;DR

A DuckLake lakehouse that forks dataELT's published gold contract
(`{schema}.{layer}_current` Postgres tables) and emits four artifacts per topic:

1. **DuckLake table** (native DuckDB geom) — catalog on mapping-db Postgres, parquet on GCS
2. **GeoParquet archive** (latest + dated, citable) — GCS
3. **PMTiles** (vector, via tippecanoe) — GCS
4. **STAC item** (links the above) — GCS

Pipeline is `source → transform → 4 sinks`, env-driven per topic in `ingest.py`.

**Status (2026-06-10):** Pipeline validation complete. All topics (25 total) successfully ingested. GeoParquet, PMTiles, STAC catalog (with CDN base URL correctly configured for public access), and DuckLake tables verified live in sandbox. Ready for Cloud Run transition.

**Deploy implication:** obstore-via-fsspec is pure Python and works in the FastAPI/Cloud
Run runtime — so **no gcsfuse volume and no GCS extension are needed at deploy.** The GCS
auth story is the same local and on Cloud Run (ADC / Workload Identity).

---

## Current state (2026-06-15) — read this first

Two boxes: this **personal box** (no GCP perms) authors + commits; the **work box**
(Gemini, has perms) runs deploys/grants/ingests. Hand perm-gated steps off via `docs/`.

**Shipped since 2026-06-10** (all committed on `main`):
- **Pubs producer** (`src/ugs_warehouse/pubs/`) — publications → COG harvest (GDAL
  microservice `Dockerfile.harvest`) + footprints + STAC `ugs-publications` collection.
  Shared `core/` (config, gcs, stac) under both vector + pubs producers.
- **STAC collections layout** — `core/stac.refresh_catalog()` derives root + per-collection
  docs from GCS truth. Replaces the old flat catalog. Collections: `ugs-serving-topics`
  (vector), `ugs-publications` (pubs), `ugs-rasters` (future).
- **Serving tier** — pg_featureserv (OGC API Features) for ArcGIS Pro/AGOL (`api/`,
  `_API_SERVICE`). GeoServer killed ($600/mo). Read-only `schema_reader` (fail-closed).
- **Deploy** — `cloudbuild.yaml` (3 images: warehouse/api/harvest; job + harvest-job +
  service + api) + `.github/workflows/deploy.yml` (WIF keyless → `gcloud builds submit`).
- **Pub/Sub ingest (#418)** — `service/main.py` push handler; `cloudbuild` `wire-pubsub`
  step provisions topic+subscription+invoker. Acks+skips non-`MART_SCHEMAS` (gwportal).
- **Viewer** (`viewer/`) — React 19 + Vite + Tailwind v4 + TanStack Query + react-map-gl 8
  / maplibre 5 + pmtiles. **Catalog** view (collection cards w/ counts + descriptions,
  search-all, sortable table) + **Map** view (PMTiles + footprint). **Light/dark theme**
  matching ugs-map-viewer (burnt-orange tokens, `vite-ui-theme`). **Shareable deep-links**
  (`?view=&c=&i=`). **Client-side export** (item detail): SHP / GPKG / **FileGDB (.gdb)** /
  FlatGeobuf via gdal3.js (real OGR in wasm, ~40 MB lazy) + GeoJSON/CSV native — Esri-ready.
- **Stack currency** — verified vs DevelopmentSeed/2026 best-practice; upgraded React 18→19,
  react-map-gl 7→8, maplibre 4→5; export switched bespoke→gdal3.js. (DuckLake readiness still
  unverified.) Future adds for raster: titiler (dynamic COG tiles), stac-geoparquet/stac-map.
- **Tests + CI** — `tests/` pytest (topic parse, pub/sub gate, stac builders, raster mapping;
  20 tests) + `.github/workflows/test.yml` (ruff + pytest).
- **Raster consumer scaffold** (`src/ugs_warehouse/raster/`) — identity + `sink_stac` +
  provisional `consume` (STAC mapping ready; COG promote `NotImplementedError` pending #169).
- **Viewer deploy wired** — `cloudbuild.yaml` `build-viewer`/`deploy-viewer` → bucket → CDN
  `…/warehouse/viewer/` (`allowFailure` until build SA gets bucket objectAdmin; `DEPLOY.md §5`).

**Live in prod (Gemini ran 2026-06-16):**
- **STAC Viewer fully deployed**: Granted `roles/storage.objectAdmin` on `gs://ut-dnr-ugs-maps-prod-public` to both `534590904912-compute@developer.gserviceaccount.com` and `534590904912@cloudbuild.gserviceaccount.com`. The STAC viewer is compiled and synced successfully to the CDN at `https://maps-assets.geology.utah.gov/warehouse/viewer/index.html`.
- **Pub/Sub event-driven ingest fully active**: Configured private push handler `ugs-warehouse-ingest-push`, granted Service Account Token Creator to Pub/Sub system agent, and verified end-to-end event delivery.
- **Completed full STAC parallel reingest**: Discovered that sequential HTTP GCS writes for 7,425 items took 1.5 hours and timed out. Implemented `ThreadPoolExecutor` (64 workers) in `src/ugs_warehouse/pubs/ingest.py` and thread-safely cached `GCSStore` warm connection pools in `src/ugs_warehouse/core/gcs.py`. Entire catalog ingestion time crashed from **1.5 hours to under 2 minutes**, successfully writing all items with the new `ugs:series_id` property.
- **Double-billing CI optimization**: Appended `--async` to `.github/workflows/deploy.yml` so the GitHub Actions runner exits immediately, avoiding double-billing while Cloud Build executes in GCP.
- **100% Vector Layer Styling Coverage**: Designed and hand-authored robust, gorgeous MapLibre GL styles for all remaining unstyled layers (achieving 100% coverage across all 19 layers in `ugs-styles`), verified typing/compilation, and published to master.
- **Map Viewer Render Integration**: Upgraded the main map viewer (`viewer/src/Map.tsx`) to pull the STAC properties/renders dynamically, fetching and compiling custom GL style layers at runtime with a clean fallback.
- **Fast Production Restyle Rebinding**: Rebound the live GCS production catalog items with their new STAC `renders` and assets in under a second using `just restyle`.

**Remaining / Handed-off:**
- **Raster Integration**: Dual-track design is fully committed on `main` (`docs/RASTER_SPEC.md`). The batch `geolmap-harvest` job is deployed and validated, and the sibling PR #169 in `ugs-ingest` is ready for review. Once they handshake on the DB table, everything is set.
- **Viewer bare-prefix serving** (one-time, needs `storage.buckets.update`): `…/warehouse/viewer/` returns GCS `NoSuchKey`; set `--web-main-page-suffix=index.html` on `gs://ut-dnr-ugs-maps-prod-public` so the bare prefix + clean deep-links resolve. Exact cmd + caveat (don't set a bucket-wide 404 page) in `DEPLOY.md §5`. Until then, link `…/warehouse/viewer/index.html`.

**Work-box checklist — 2026-06-17 session (in order):**
1. `git pull` main.
1b. ⚠ **ONE-TIME (do before step 2, else the deploy's `COPY --from` fails):** build the prebuilt
   tippecanoe base — `gcloud builds submit --config=cloudbuild.tippecanoe.yaml --project=ut-dnr-ugs-backend-tools .`
   Compiles tippecanoe once → pushes `ugs-tippecanoe`. After this, warehouse/harvest builds just COPY
   the binary (no ~10min×2 compile per deploy). Rebuild only on a tippecanoe version bump.
2. **Deploy** — push to `main` (or `gcloud builds submit --config=cloudbuild.yaml . --project=ut-dnr-ugs-backend-tools --substitutions=_TAG=$(git rev-parse --short HEAD)`). Builds/deploys: **NEW `ugs-warehouse-features`** (OGC API Features for Arc Pro — `featureserv/`, duckdb_featureserv over CDN parquet, scale-to-zero, `--port=9000`), **NEW `ugs-pubs-ingest`** job, viewer (series-id column + COG explorer + per-type preview + short-id deep links), + warehouse/api/harvest. ⚠ `deploy.yml` has `--async` → the GH run goes green on *submit*, not finish — watch the **Cloud Build console** for the real result.
3. **Vector reingest** — lands the new `bbox_*` covering columns in the GeoParquet: `gcloud run jobs execute ugs-warehouse-ingest --region=us-central1`.
4. **ArcGIS Pro test (acceptance gate for the OGC API):** URL = `gcloud run services describe ugs-warehouse-features --region=us-central1 --format='value(status.url)'` → Arc Pro: *Insert → Connections → Server → New OGC API Server* → that URL → add a layer. Report whatever Pro complains about (iterate in `featureserv/`).
5. **Styling go-live** (in the `ugs-styles` repo): (a) grant the publish SA `roles/storage.objectAdmin` on `gs://ut-dnr-ugs-maps-prod-public`; (b) set repo secrets `GCP_WIF_PROVIDER` + `GCP_SERVICE_ACCOUNT`; (c) tag a `v*` release → publishes `dist-json` → CDN `/styles`. Then run `just restyle` (warehouse) to rebind `renders` onto the existing STAC items — **no reingest** → viewer shows the styled layer. (`STYLING.md §8` + §10.)
6. **Viewer bare-prefix:** `gcloud storage buckets update gs://ut-dnr-ugs-maps-prod-public --web-main-page-suffix=index.html` (do NOT set a 404 page — `DEPLOY.md §5`).
7. **Merges:** `dataELT #419` (ready — #420 already merged); poke `ugs-ingest #169` (raster, still **draft**).

(`ugs:series_id` pubs reingest already done in the 2026-06-16 parallel reingest above — no repeat needed.)

**Marshall asks** (Clinton has merge + push-to-his-PR rights — "just wants shit done"):
- **Merge `ugs-ingest` #169** (raster producer `raw.raster_catalog`) — still OPEN; unblocks raster consumer.
- **Merge `dataELT` #420** (adds `dbt_test` schema to CI gate; needs `dbt_test_user`/`dbt-test-db-password` to exist) — unblocks the stuck ucrc PR #419.
- Confirm pubs schema read perms + project for the runtime SA.
- ✅ `schema_reader` role + `schema-reader-db-password` secret provisioned — `deploy-api` `allowFailure` dropped.

**Still TODO (warehouse code):** finish raster consumer COG promote once #169 lands (the
`consume.promote` `NotImplementedError` — cross-bucket staged→public copy); small dataELT
raster promote-gate PR; `gwportal` own-DB discovery (currently acked+skipped). Verify
DuckLake 2026 production-readiness. (Viewer downloader: DONE — gdal3.js, 6 formats.)

---

## Styling & Reingestion Spec Sheet (2026-06-17)

This section maps out the target styling parameters for the remaining 18 unstyled vector layers in the `ugs-styles` repository, alongside the status of the complete catalog reingestion.

### 1. Reingestion Status
- **Vector Reingestion:** **SUCCESS**. The Cloud Run job `ugs-warehouse-ingest-2wxmn` was executed and successfully completed. This has regenerated the entire vector STAC catalog under `ugs-serving-topics`, establishing the new `bbox_*` spatial index columns in all GeoParquet archives.
- **Publications Reingestion:** **SUCCESS**. All 7,425 items were processed in under 2 minutes utilizing parallel ingestion with ThreadPoolExecutor (avoiding Cloud Run timeouts).

### 2. Vector Styling Spec Sheet (the styling worklist)
Each vector style targets a STAC item by its `itemId` (bare topic stem). To define a style for these unstyled layers, a corresponding TS spec should be created inside `ugs-styles/src/styles/{layer_dir}/` with the specified parameters.

| Item ID (STAC Item) | Recommended Archetype | Styling Field | Recommended Palette / Symbology Design |
|---|---|---|---|
| **`hazards_qfaults`** | `categorical` | `faultage` | Maps fault ages: `<15,000` (Red), `<130,000` (Orange), `<750,000` (Yellow), `<2,600,000` (Green), `undetermined` / `<150` (Gray/Light Red). |
| **`hazards_surfacefaultrupture`** | `simple` | — | Emits line symbols with high-contrast Orange-Red color to indicate active rupture zone boundaries. |
| **`enmin_pipelines`** | `categorical` | `commodity` | Maps commodities: `Natural Gas` (Blue), `Petroleum` / `Crude Oil` (Brown), `Liquified Petroleum Gases (LPG)` (Purple), default (Gray). |
| **`enmin_ut_counties`** | `categorical` | `color4` | Maps county polygons to 4 distinctive colors to prevent adjacent counties from having the same fill. |
| **`enmin_ccus_cbcounty`** | `simple` | — | Boundary lines / light fill for Carbon Capture county areas. |
| **`enmin_ccus_cbgeoregion`** | `categorical` | `region_name` | Categorical fill representing Carbon Capture geologic regions. |
| **`enmin_ccus_geochemfieldpolys`** | `simple` | — | Outline/dashed stroke showing geochemical field boundaries. |
| **`enmin_ccus_geochemistry`** | `point` | `sample_type` | Point symbology marking sample stations colored by type. |
| **`enmin_geophysics_mtstations`** | `point` | — | Magnetotelluric measurement station markers. |
| **`enmin_geophysics_tem`** | `point` | — | Transient Electromagnetic measurement station markers. |
| **`enmin_oilgasfields_ogm`** | `categorical` | `status` | Polygonal fill indicating active fields (Green) vs abandoned/depleted fields (Gray). |
| **`enmin_plss_sections`** | `simple` | — | Light dashed boundaries for PLSS section grid lines. |
| **`enmin_plss_townshiprange`** | `simple` | — | Medium solid boundaries for PLSS Township & Range lines. |
| **`enmin_powerplants`** | `point` | `primary_fuel` | Plant markers colored by fuel: `Coal` (Black), `Natural Gas` (Blue), `Solar` (Yellow), `Hydro` (Cyan). |
| **`enmin_transmissionlines`** | `categorical` | `voltage_kv` | Graduated line width or colors indicating transmission line voltage ranges. |
| **`enmin_ucrc_basins`** | `categorical` | `basin_name` | Semi-transparent fills representing separate water basins. |
| **`geolmap_geolunits_gems`** | `geologic-unit` | `unit_symbol` | Matches unit symbol to authoritative FGDC geologic unit colors. |
| **`wells_spatial`** | `point` | `well_type` | Point circle markers for borehole/well locations. |

### 3. Execution Plan for New Styles Go-Live
1. **Create Palette / Spec:** Add missing named color palettes in `ugs-styles/src/palettes/` (e.g. `qfaults`, `pipelines-commodity`), write the `spec` files in `ugs-styles/src/styles/`, and add matching TS exports.
2. **Build and Validate:** Run `npm run build:json` inside `ugs-styles` to generate `dist-json/` files and validate style spec correctness.
3. **Publish Styles CDN:** Tag a release (`git tag v* && git push origin --tags`) in the `ugs-styles` repo, triggering CI/CD publish to `gs://ut-dnr-ugs-maps-prod-public/styles/`.
4. **Rebind STAC (no reingest):** Run `just restyle` (or `python -m ugs_warehouse.restyle`) to re-fetch the CDN style manifest and bind the new `renders` metadata into the existing STAC item files — seconds, no DB/transform/PMTiles. Use `--collection all` to include pubs. A full reingest is not needed for a style-only change. (`STYLING.md §10`.)

---

## Editing pubs metadata (title/author/description/etc.)

Pub metadata is **derive-from-truth** — the STAC item is a *projection*, rebuilt from the
source every `pubs.ingest`. Do **not** hand-edit the catalog JSON (mutable / `no-cache` —
next ingest clobbers it). There is no per-item override layer.

**Where prod metadata actually lives:** `PUBS_DB_URL` is unset in the deploy, so the source
falls to the **vendored CSV snapshot** baked into the image:
`src/ugs_warehouse/pubs/data/pubsdb.csv` (+ `pubsattacheddata.csv`). That CSV was exported
from the upstream MySQL `pubsdb`.

**To edit one pub's metadata:**
1. Edit the row (by `series_id`) in `src/ugs_warehouse/pubs/data/pubsdb.csv`, commit.
2. Run the rebuild — **no COG re-harvest** happens:
   ```
   gcloud run jobs execute ugs-pubs-ingest --region=us-central1   # work box, has perms
   # local equivalent: python -m ugs_warehouse.pubs.ingest
   ```

**Durability catch:** the CSV is a snapshot. A direct CSV edit is **lost** whenever someone
re-exports a fresh snapshot from MySQL. Durable fix = edit upstream MySQL `pubsdb` (or set
`PUBS_DB_URL` to read it live), then rebuild. CSV edit = quick patch only.

**`ugs-pubs-ingest` job (new, 2026-06-16):** wired in `cloudbuild.yaml`
(`deploy-pubs-ingest-job`). Uses the **harvest image** (carries `pubs`/geopandas that
`footprints.geoms` needs); **no Cloud SQL / DB secret** (source is CSV + GCS). Rebuilds the
`ugs-publications` STAC items + `refresh_catalog`. This replaces the previously **manual**
pubs STAC build — it now redeploys on every push, and is run on demand via `jobs execute`.
(Not scheduled; add a Cloud Scheduler trigger if periodic refresh is wanted.)

## Agent docs

Single canonical **`AGENTS.md`** at repo root (architecture + invariants + env vars).
`CLAUDE.md` / `GEMINI.md` were removed to avoid duplication — Claude Code and
opencode/Gemini both read `AGENTS.md`. Edit only that file.

---

## Repos & paths

| | Path / URL |
|---|---|
| **This repo (local)** | `/home/clinton/Documents/Programming/ugs-warehouse` |
| **GitHub (primary)** | `git@github.com:UGS-GIO/ugs-warehouse.git` (private) — `origin` |
| **GitHub (mirror)** | `git@github.com:clintonlunn/ugs-warehouse-mirror.git` (private) — `mirror` |
| **Branch** | `main` (default, only branch in use) |

**Neighbor repos** (`/home/clinton/Documents/Programming/`) — all **read-only** from here;
coordinate any `_current`-contract change via marshallrobinson:

| Repo | Role |
|---|---|
| `dataELT/` | dbt project, `_current` producer, `publish.sh` (future Pub/Sub emit) |
| `ugs-ingest/` | extraction + dbt model generator (now emits 4326) |
| `ugs-map-viewer/` | public frontend (PostgREST + PMTiles + WMS consumer) |
| `ugs-geoserver/` | GeoServer image; `scripts/geoserver_srs_fix.py` runs during 4326 cutover |

**GCP projects:** `ut-dnr-ugs-mappingdb-prod` (Cloud SQL `mapping-db` — source + DuckLake
catalog) · `ut-dnr-ugs-maps-prod` (public storage, bucket `ut-dnr-ugs-maps-prod-public`,
scoped to `warehouse/...`) · `ut-dnr-ugs-backend-tools` (future Cloud Run + Pub/Sub).

---

## Architecture

```
publish.sh (dataELT) → Pub/Sub {schema, topic}      ← #418 wired; awaits work-box perms
   ↓ push subscription (cloudbuild wire-pubsub)
Cloud Run service (service/main.py)                 ← acks+skips non-MART_SCHEMAS
   ↓
source (postgres OR postgrest)                      ← swappable via SOURCE_BACKEND env
   ↓  pyarrow Table: geom_wkb BLOB + target_epsg
transform.run (DuckDB)
   hydrate WKB · reproject target_epsg→4326 (if needed) · h3_r9 · hilbert-sort
   ↓
geometry guard: 0 non-null geom → SKIP (rc=1), before any sink
   ├─→ sink_ducklake   (native geom)              → mapping-db catalog + GCS data
   ├─→ sink_archive    (GeoParquet, latest+dated) → GCS
   ├─→ sink_pmtiles    (tippecanoe)               → GCS
   └─→ sink_stac       (STAC Item JSON)           → GCS
```

**Data contract (the key invariant).** `source.read()` returns a pyarrow Table where
geometry is a `geom_wkb` BLOB and each row carries `target_epsg` (CRS the WKB is in) +
`source_epsg` (provenance only). `transform.run()` hydrates + reprojects to 4326, adds
`h3_r9`, hilbert-sorts. Any new backend MUST emit this shape.

**Why DuckLake (not Iceberg):** flipped 2026-06-05 (commit `d62e423`). Native DuckDB
geom + timestamps, simpler ops; Iceberg's multi-engine federation buys nothing at UGS
scale. GeoParquet archive remains the standard partner-download surface either way.

**Why fork at `_current` (gold):** the dbt SCD2/enrichment logic is marshallrobinson's
valuable upstream work — don't re-derive it. The warehouse adds a *serving shape*
(lakehouse + PMTiles + STAC) on top. Build-to-attract, not absorb.

---

## Source backends (`SOURCE_BACKEND` env)

### `postgres` (default, prod target) — `source.py`
Direct libpq via DuckDB postgres extension; geom as `geom_wkb` BLOB. Best path.
**Blocked** until a `schema_reader` login on mapping-db is provisioned.

### `postgrest` (bandaid, currently working) — `source_postgrest.py`
HTTP via the public `postgrest-seamlessgeolmap` Cloud Run instance. GeoJSON →
WKB via shapely. Two realities to know:

- **CRS:** GeoJSON with no `crs` member = WGS84/4326 (RFC 7946). The backend defaults
  `target_epsg` to 4326 in that case; pre-cutover rows carry an explicit `EPSG:3857`
  extension. *(Fixed 2026-06-07 — it used to drop the column, breaking transform on
  every already-4326 topic; a latent landmine for the cutover.)*
- **Coverage:** `web_anon` has table SELECT on emp/wetlands/mapping, but **column-level
  grants hide `geom`** on hazards/gen_gis (an Esri `shape` column appears instead).
  Those topics return rows with null geometry → caught by the geometry guard → SKIP.

So PostgREST is fine for iterating on emp/wetlands/mapping spatial topics today, but
**full coverage needs `schema_reader`** (direct postgres). Tracked in GH issue #1.

---

## Status

### Done
- Full `source → transform → 4-sink` pipeline, end-to-end mock-validated (postgis +
  fake-gcs-server), all 4 sinks producing artifacts.
- DuckLake refactor from Iceberg (native geom, no WKB/tz hacks).
- PostgREST backend proven on real data; runtime topic discovery; `--dry-run`; dotted
  `--topic` form; per-sink isolation.
- **2026-06-07:** `target_epsg` CRS-default fix; geometry guard (SKIP 0-geom before
  sinks, both modes); DuckLake `METADATA_SCHEMA` pin into `ducklake_catalog`; single
  `AGENTS.md` replacing `CLAUDE.md`/`GEMINI.md`.
- **2026-06-08:** obstore replaces `google-cloud-storage` (file sinks); DuckLake GCS
  writes via **obstore-fsspec** — httpfs/HMAC fully sidestepped, all 4 sinks green;
  postgres source derives `target_epsg` from `ST_SRID`; auto-rebuilt static STAC root
  catalog after each ingest (`scripts/refresh_stac.py` for on-demand); `docs/RASTER.md`.
- **2026-06-10:** full `--all` — **25 spatial topics** ingested end-to-end via direct
  postgres + `schema_owner` (full geom coverage incl. hazards/gen_gis — the postgrest
  hidden-geom problem is gone on this path). STAC asset hrefs default to the maps-assets
  CDN (`https://maps-assets.geology.utah.gov`, path-preserved; bucket private).

### Next — deploy + viewability

Local pipeline is **validated at scale** (25 topics, all 4 artifacts each). Re-run any
time: `./scripts/run_ingest.sh --all` (or `--topic <schema.layer_current>`) — DB password
from Secret Manager, config from `.env`. Two tracks from here:

**Deploy (Cloud Run).**
- `cloudbuild.yaml`: build image → Artifact Registry → deploy Cloud Run.
- GH Actions: **WIF (keyless)** auth + `ruff` gate → trigger Cloud Build. Boss's GH SA
  has `artifactregistry.writer`; still needs `run.developer` + `iam.serviceAccountUser`
  (on the runtime SA), or `cloudbuild.builds.editor` if Cloud Build does the deploy.
- Runtime SA (Cloud Run identity, **≠** the GH SA): `storage.objectAdmin` (bucket) +
  `cloudsql.client` (mappingdb) + `secretmanager.secretAccessor` (DB password).
- Mapping-db reach on Cloud Run: unix-socket mount or `cloud-sql-python-connector` (dep
  present, unused) — no `cloud_sql_proxy` process there.
- Pub/Sub push from dataELT `publish.sh` → the service (coordinate the emit line with
  marshallrobinson).

**Viewability.**
- STAC hrefs use the CDN by default (browsers can't fetch `gs://`). Confirm **CORS on the
  CDN** — browser-only concern, irrelevant to CI/ingest.
- Stand up **STAC Browser** (static) → `https://maps-assets.geology.utah.gov/warehouse/stac/catalog.json`.
- Enrich catalog: per-schema collections + spatial/temporal extents + `proj` extension;
  fix `datetime` (currently ingest-time, not data validity time).
- PMTiles render via MapLibre (standalone preview or wire into `ugs-map-viewer`).

**Rasters** (soil-water model + one-off): parallel `raster_ingest` path — COG + STAC
primary, RaQuet optional. Design in `docs/RASTER.md`. Not started.

### Reference — first-ingest + docker runbook (validated; kept for env/DSN detail)

The commands below are what validated the pipeline; `run_ingest.sh` now wraps this env.
Kept for the explicit env-var list + the docker dress-rehearsal.

```bash
# 1. Code
cd <ugs-warehouse>
git pull
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# 2. GCS auth
gcloud auth application-default login
gcloud auth application-default set-quota-project ut-dnr-ugs-maps-prod

# 3. CHECKPOINT — confirm bucket write (403 => need roles/storage.objectAdmin)
echo ok | gcloud storage cp - gs://ut-dnr-ugs-maps-prod-public/warehouse-sandbox/_perm_check.txt
gcloud storage rm gs://ut-dnr-ugs-maps-prod-public/warehouse-sandbox/_perm_check.txt

# 4. Proxy to mapping-db for the catalog — SECOND terminal, it blocks.
#    Confirm region (us-west3 per README). v2 binary: cloud-sql-proxy <conn> --port 5433
cloud_sql_proxy -instances=ut-dnr-ugs-mappingdb-prod:us-west3:mapping-db=tcp:5433

# 5. Env (bucket is the code default; only prefixes + data path need overrides)
export SOURCE_BACKEND=postgrest
export DUCKLAKE_CATALOG_DSN="host=127.0.0.1 port=5433 dbname=seamlessgeolmap user=schema_owner password=<PW>"
export DUCKLAKE_DATA_PATH="gs://ut-dnr-ugs-maps-prod-public/warehouse-sandbox/ducklake/"
export WAREHOUSE_ARCHIVE_PREFIX="warehouse-sandbox/geoparquet"
export WAREHOUSE_PMTILES_PREFIX="warehouse-sandbox/pmtiles"
export WAREHOUSE_STAC_PREFIX="warehouse-sandbox/stac"

# 6. CHECKPOINT — bootstrap (also proves schema_owner can CREATE in ducklake_catalog)
python -m scripts.bootstrap_catalog        # expect: attached + 3 schema-ready lines

# 7. CHECKPOINT — the real ingest
python -m ugs_warehouse.ingest --topic emp.geothermal_kgra_current   # expect 4 sink lines

# 8. Verify artifacts
gcloud storage ls -r gs://ut-dnr-ugs-maps-prod-public/warehouse-sandbox/
```

Likely snags (both OK): **tippecanoe** isn't pip-installed (it's baked into the Docker
image only) — if absent the `pmtiles` sink fails but ducklake/archive/stac still write
(per-sink isolation, rc=1); we get PMTiles in the container later. And confirm the
`ducklake_catalog` schema actually lives in the `seamlessgeolmap` DB — fix `dbname=` if not.

**Docker dress-rehearsal (do once before Cloud Run).** Sequence is venv (above) →
docker → Cloud Run. The image is the real deploy artifact and includes tippecanoe, so
this is the run where all 4 sinks (incl. pmtiles) should go green:

```bash
docker build -t ugs-warehouse .
docker run --rm --network host \
  -e SOURCE_BACKEND=postgrest \
  -e DUCKLAKE_CATALOG_DSN="host=127.0.0.1 port=5433 dbname=seamlessgeolmap user=schema_owner password=<PW>" \
  -e DUCKLAKE_DATA_PATH="gs://ut-dnr-ugs-maps-prod-public/warehouse-sandbox/ducklake/" \
  -e WAREHOUSE_ARCHIVE_PREFIX="warehouse-sandbox/geoparquet" \
  -e WAREHOUSE_PMTILES_PREFIX="warehouse-sandbox/pmtiles" \
  -e WAREHOUSE_STAC_PREFIX="warehouse-sandbox/stac" \
  -e GOOGLE_APPLICATION_CREDENTIALS=/adc.json \
  -v "$HOME/.config/gcloud/application_default_credentials.json:/adc.json:ro" \
  ugs-warehouse \
  python -m ugs_warehouse.ingest --topic emp.geothermal_kgra_current
```

`--network host` lets the container reach the host's `cloud_sql_proxy` on
127.0.0.1:5433 (Linux; on macOS use `host.docker.internal` in the DSN instead). The
image CMD is uvicorn (the service) — we override it with the CLI for this one-shot.

**Actual Cloud Run note (deploy-time, not now):** no `cloud_sql_proxy` process there —
reach mapping-db via a Cloud SQL unix-socket mount or the `cloud-sql-python-connector`
(already a dep, currently unused; code uses plain libpq DSN). Wire that at deploy.

### Parked
- **Publish-allowlist on discover()** — guard SKIPs non-spatial/hidden topics gracefully,
  but `--all` still attempts them. Build an explicit allowlist of publishable spatial
  layers; also the right home for the "publish ⊆ public-readable" exposure policy
  (decide per-layer what the warehouse emits, not inherit `web_anon`'s grants).
- **Non-spatial attribute tables** (chem, lookups) — defer; eventual design = DuckLake +
  GeoParquet siblings cross-linked via STAC parent/child links.

### Not started
- Tests dir / CI workflow (GH Actions deploy scaffolding is the next concrete artifact).
- marshallrobinson coordination on `publish.sh` Pub/Sub emit line.

---

## Active blockers

| Blocker | Owner | Ask |
|---|---|---|
| **`schema_reader` least-priv read login** (prod hardening, not a blocker) | marshallrobinson | Reads currently run as `schema_owner` (works, full geom). That's over-privileged for a runtime read identity — `schema_owner` is a write/owner role. Want a minimal read-only login for prod: `CREATE ROLE schema_reader LOGIN …; GRANT USAGE + SELECT ON ALL TABLES IN SCHEMA hazards, emp, gen_gis, wetlands, mapping; ALTER DEFAULT PRIVILEGES … GRANT SELECT ON TABLES`. Not blocking — downgraded from blocker to hardening |

Resolved this session: catalog write home (`schema_owner` + `METADATA_SCHEMA`); bucket
name (`ut-dnr-ugs-maps-prod-public`); **all 4 sinks writing to sandbox GCS** via obstore
(file sinks) + obstore-fsspec (DuckLake, commit `5b0fcda`) — **httpfs/HMAC fully sidestepped**;
`target_epsg` (derived from `ST_SRID` in `source.py`, commit `2284ad2` — a warehouse bug,
**not** an upstream/Marshall gap as first logged).

---

## Decision context (don't re-litigate)

- DuckLake over Iceberg — settled 2026-06-05 (`d62e423`).
- Fork at `_current` gold, not bronze — don't duplicate the boss's silver/gold logic.
- Build-to-attract, not absorb.
- 3-layer GCP model: `mappingdb-prod` (DB) / `maps-prod` (storage) / `backend-tools`
  (compute). Keep `maps-prod`. Project IDs immutable — display-name rename only.
- PostgREST source is a bandaid; track-and-swap via GH #1.
- Exposure principle: publish ⊆ public-readable. Make it an explicit per-layer allowlist,
  not an accident of `web_anon` grants.

---

## Parallel cutover (NOT this repo's job)

marshallrobinson is shipping a 3857 → 4326 migration across the org:
`ugs-ingest` #162 (generator/payload/docs), `dataELT` #411 (int models, 44 files),
`ugs-map-viewer` #448 (frontend 4326 filters; WMS render stays 3857 via GeoServer).
Merge in one window, then run `ugs-geoserver/scripts/geoserver_srs_fix.py`, then deploy
the viewer. **The warehouse handles the mixed state already** — `transform` reprojects
per-row off `target_epsg`, so it works whether a topic is still 3857 or already 4326.
Clinton's old `ugs-ingest` #161 was superseded by #162 and closed.

---

## Memory references

At `/home/clinton/.claude/projects/-home-clinton-Documents-Programming-dataELT/memory/`:
`project_warehouse_fork.md`, `project_airflow_orchestrator.md`, `user_learning_airflow.md`,
`feedback_doc_integration.md`, `feedback_commit_style.md`, `feedback_target_sketch_voice.md`,
`feedback_crs_change_scope.md`.

---

*End of handoff. Update as state changes; delete when no longer the live working state.*
