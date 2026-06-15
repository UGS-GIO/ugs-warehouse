# ugs-warehouse — Handoff

**Purpose:** lets another agent (or Clinton on a future session) pick up cold —
current state, what's done, what's blocked, the immediate next move.

**Live working doc** — tracked so it syncs across machines. Refresh as state changes;
retire once deployed. Last refreshed **2026-06-15**.

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

**Live in prod (Gemini ran 2026-06-15):** images built, jobs+services deployed, Pub/Sub
`ugs-warehouse-ingest-push` active (OIDC), **vector ingest run → prod catalog has both
`ugs-publications` + `ugs-serving-topics` (19→25 items)**. Viewer (local dev) confirmed
rendering live prod. #418 merged (squash).

**Remaining (work box / Gemini, has perms):**
1. **Grant build SA bucket objectAdmin** → next build publishes the viewer to the CDN (`DEPLOY.md §5`).
2. **Pub/Sub grants** if not yet done (`DEPLOY.md §4`) — confirm `ugs-warehouse-ingest-push` end-to-end.

**Marshall asks** (Clinton has merge + push-to-his-PR rights — "just wants shit done"):
- **Merge `ugs-ingest` #169** (raster producer `raw.raster_catalog`) — still OPEN; unblocks raster consumer.
- **Merge `dataELT` #420** (adds `dbt_test` schema to CI gate; needs `dbt_test_user`/`dbt-test-db-password` to exist) — unblocks the stuck ucrc PR #419.
- Provision `schema_reader` role + `schema-reader-db-password` secret (unblocks api deploy, currently `allowFailure`).
- Confirm pubs schema read perms + project for the runtime SA.

**Still TODO (warehouse code):** finish raster consumer COG promote once #169 lands (the
`consume.promote` `NotImplementedError` — cross-bucket staged→public copy); small dataELT
raster promote-gate PR; `gwportal` own-DB discovery (currently acked+skipped). Verify
DuckLake 2026 production-readiness. (Viewer downloader: DONE — gdal3.js, 6 formats.)

---

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
