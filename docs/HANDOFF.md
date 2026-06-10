# ugs-warehouse — Handoff

**Purpose:** lets another agent (or Clinton on a future session) pick up cold —
current state, what's done, what's blocked, the immediate next move.

**Live working doc** — tracked so it syncs across machines. Refresh as state changes;
retire once deployed. Last refreshed **2026-06-07**.

---

## TL;DR

A DuckLake lakehouse that forks dataELT's published gold contract
(`{schema}.{layer}_current` Postgres tables) and emits four artifacts per topic:

1. **DuckLake table** (native DuckDB geom) — catalog on mapping-db Postgres, parquet on GCS
2. **GeoParquet archive** (latest + dated, citable) — GCS
3. **PMTiles** (vector, via tippecanoe) — GCS
4. **STAC item** (links the above) — GCS

Pipeline is `source → transform → 4 sinks`, env-driven per topic in `ingest.py`.

**State (2026-06-08):** **All four sinks green end-to-end** on
`emp.geothermal_kgra_current` via direct Postgres (`schema_owner`) — GeoParquet, PMTiles,
STAC, **and the DuckLake table** all landing in sandbox GCS. **httpfs is fully off the GCS
path:** the three file sinks `COPY` to a local temp then upload via **obstore**; DuckLake
routes its DATA_PATH chunk writes through **obstore via an fsspec filesystem** registered
on the connection (`catalog.attach`, commit `5b0fcda`) — ADC auth, no HMAC. This sidesteps
the org HMAC block entirely (DuckDB httpfs only auths GCS via HMAC; confirmed `gcloud
storage hmac create` denied). Other fixes: `source.py` derives `target_epsg` from
`ST_SRID`; `google-cloud-storage` dropped for obstore (less lock-in, CNG-aligned);
`OVERRIDE_DATA_PATH` for sandbox cataloging; reverted a hardcoded macOS ADC-path hack.
Not yet deployed.

**Deploy implication:** obstore-via-fsspec is pure Python and works in the FastAPI/Cloud
Run runtime — so **no gcsfuse volume and no GCS extension are needed at deploy.** The GCS
auth story is the same local and on Cloud Run (ADC / Workload Identity).

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
publish.sh (dataELT) → Pub/Sub {schema, topic}      ← future trigger; not wired yet
   ↓ push subscription
Cloud Run service (service/main.py)
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

### Next (immediate) — first real ingest, work-box runbook

Decisions: reuse bucket `ut-dnr-ugs-maps-prod-public` under a `warehouse-sandbox/`
prefix (promote to `warehouse/` once it looks right); run manually on the work box
to validate the full pipeline before any Cloud Run deploy. The four sinks write:
DuckLake table (parquet chunks on GCS + metadata rows in Postgres), GeoParquet
archive (parquet), PMTiles, STAC json — so two of four emit parquet, not just STAC.

Run top to bottom; check output after each **CHECKPOINT** before continuing.

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
- **GCP deploy infra** (SA, Artifact Registry, Cloud Run, Pub/Sub) — after first real
  ingest validates against real data.

### Not started
- Tests dir / CI workflow.
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
