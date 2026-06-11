# AGENTS.md

Single agent-instructions file for this repo (the `AGENTS.md` standard). If a
tool defaults to a different name (Claude Code → `CLAUDE.md`, Gemini → `GEMINI.md`),
point it here via that tool's config instead of duplicating this file.

## What this is

A DuckLake lakehouse for UGS. It forks at dataELT's published gold contract — the
Postgres `{schema}.{layer}_current` serving tables — and emits four artifacts per
topic: a DuckLake table (native geom), a GeoParquet archive, PMTiles, and a STAC
item. It does **not** re-derive silver/gold; it adds a serving shape on top of an
existing dbt mart contract owned upstream (marshallrobinson). See `docs/HANDOFF.md`
for live working state, blockers, and decision history (not committed).

## Commands

```bash
pip install -e ".[dev]"          # editable install + pytest/ruff

ruff check src service scripts    # lint (line-length 100)

# Manual ingest — dotted topic form schema.layer_current
python -m ugs_warehouse.vector.ingest --topic hazards.hazards_qfaults_current
python -m ugs_warehouse.vector.ingest --topic emp.geothermal_kgra_current --dry-run
python -m ugs_warehouse.vector.ingest --all      # runtime-discover every _current in MART_SCHEMAS

python -m scripts.bootstrap_catalog        # idempotent: ATTACH DuckLake + create schemas

# Cloud Run service (Pub/Sub push handler) locally
uvicorn service.main:app --host 0.0.0.0 --port 8080
```

`--dry-run` exercises source + transform only (no GCS / catalog writes) — the safe
smoke against real `_current` data. It prints row count, 4326 bbox, and a sample row.
A topic with **0 rows carrying geometry** is reported `SKIP` (rc=1) and never reaches
the sinks — see the geometry guard below.

**Tests:** there is no test suite yet. Validation is `ruff` + `--dry-run` + the mock
smoke recipe (Docker postgis + fake-gcs-server) described in `docs/HANDOFF.md`. If you
add tests, `pytest` is already a dev dep but no `tests/` dir exists.

## Layout

Two **producers** on a **shared core**:
- `core/` — `config` (one `WAREHOUSE_BUCKET` + CDN `PUBLIC_BASE_URL` + prefixes), `gcs` (obstore
  upload/list + Cache-Control + CDN URLs), `stac` (item builder, collections hierarchy,
  derive-from-truth catalog refresh, web-map-links, titles). **Both producers emit through this.**
- `vector/` — producer A: Postgres `_current` topics → DuckLake + GeoParquet + PMTiles + STAC.
- `pubs/` — producer B (in progress): publications → COG + footprints + units + STAC. See
  `docs/INTEGRATION_GEOLMAP.md`.

One STAC catalog spans both, laid out with collections (`ugs-serving-topics`, `ugs-publications`).

## Architecture (vector producer)

Pipeline is `source → transform → 4 sinks`, orchestrated per topic in `vector/ingest.py`:

```
Pub/Sub {schema, topic} → service/main.py → ingest.ingest_topic(Topic)
   source.read(topic)            → pyarrow Table (geom as geom_wkb BLOB + target_epsg)
   transform.run(arrow)          → DuckDB conn + "transformed" view (4326, h3_r9, hilbert-sorted)
   sink_ducklake / sink_archive / sink_pmtiles / sink_stac  (each reads the view)
```

**Data contract between layers — the key invariant.** `source.read()` returns a
pyarrow Table where the geometry is a `geom_wkb` BLOB (server-side `ST_AsBinary`) and
each row carries `target_epsg` (the CRS the WKB bytes are in) and `source_epsg`
(upstream provenance only). `transform.run()` hydrates `geom_wkb` into a DuckDB
`GEOMETRY`, reprojecting `target_epsg → 4326` only if not already 4326, adds `h3_r9`
(H3 cell at res 9 from the centroid), and `ORDER BY ST_Hilbert(centroid)` so parquet
row-groups bbox-prune well. Any new source backend MUST emit this same shape.

**Swappable source backend (`SOURCE_BACKEND` env).** `ingest._backend()` picks the
module: `postgres` (default, `source.py`, direct libpq via DuckDB postgres extension)
or `postgrest` (`source_postgrest.py`, HTTP via the public PostgREST instance,
GeoJSON re-encoded to WKB via shapely). PostgREST is a **bandaid** for running without
a DB login — it has partial schema coverage (emp/wetlands/mapping only) and is slow.
Both modules expose `read(topic)` and `discover()`; transform + sinks are unaffected
by the choice. Swap back to `postgres` once DB creds land (tracked in GH issue #1).

Two PostgREST realities to know: (1) it returns GeoJSON, and per RFC 7946 a geometry
with **no `crs` member is WGS84/4326** — `source_postgrest` defaults `target_epsg`
to 4326 in that case (pre-cutover rows carry an explicit `EPSG:3857` CRS extension;
already-4326 rows omit it). (2) `web_anon` has **column-level grants that hide `geom`**
on hazards/gen_gis — those topics come back with rows but null geometry (an Esri
legacy `shape` column shows up instead), so they hit the geometry guard and SKIP.
Direct `postgres` (a `schema_reader` login) is the real fix for full coverage.

**Geometry guard.** `ingest._ingest()` checks geometry presence right after
`transform.run()` and **before** any sink: if 0 rows have non-null `geom`, it logs
`SKIP` and returns rc=1 in both dry-run and real mode. This catches genuinely
non-spatial mart tables (chemistry, lookups) and backend-hidden geom alike, so the
sinks never emit empty/broken PMTiles or null-geom parquet.

**Topics are not hard-coded.** A `Topic` is just `{schema, layer}` (`topics.py`).
There is no registry — `discover()` scans `MART_SCHEMAS` at runtime, `Topic.parse()`
handles the dotted CLI form, and `from_pubsub()` builds one from the trigger payload.
Adding a new `_current` upstream is zero-config here.

**Sinks are independent and isolated.** `ingest._ingest()` runs each sink in its own
try/except: one sink failing is logged to stderr and sets `rc=1` but never crashes the
others. The Cloud Run handler acks the Pub/Sub message even on `rc!=0` (recovery
happens on the next publish) to avoid retry storms.

**STAC catalog auto-refreshes.** A producer's stac sink emits an item via
`core.stac.write_item()`; then, after the sinks, the orchestrator calls
`core.stac.refresh_catalog()`, which **lists the item files in GCS and rewrites the root
`catalog.json` + every `collection.json`** — so the catalog stays current with no manual
regen (`scripts/refresh_stac.py` for on-demand). Derive-from-truth (lists actual items,
not a mutated shared file), so concurrent ingests converge (last writer wins, self-heals).
Logic is pure builders in `core/stac.py` (`build_item`, `_group_items`, `_collection_doc`,
`_root_doc`) — keep them pure for testing.

**Rasters** are a planned parallel pipeline (COG + STAC primary; RaQuet optional),
sharing the STAC catalog + GCS + obstore layers. See `docs/RASTER.md` — design only,
not implemented.

**DuckLake catalog** (`catalog.py`): catalog metadata lives in a DuckLake-managed
Postgres DB (mapping-db in prod, or a local docker pg for dev); parquet data chunks
land in GCS under `DUCKLAKE_DATA_PATH`. `attach()` is idempotent, loads the
`spatial/postgres/ducklake` extensions, and pins DuckLake's metadata tables into the
`METADATA_SCHEMA` (`DUCKLAKE_METADATA_SCHEMA` env, default `ducklake_catalog`) so they
never land in the catalog DB's `public` — the catalog DSN user (prod: `schema_owner`)
only needs CREATE on that one schema. `sink_ducklake` does `CREATE OR REPLACE TABLE`
per ingest — DuckLake's snapshot model keeps prior versions readable by id.

**GCS IO — do NOT re-add httpfs for GCS.** DuckDB's `httpfs` reaches GCS only via the
S3-compat API with **HMAC keys**, which org policy blocks. So nothing in this repo
writes GCS through httpfs:
- The file sinks (`sink_archive`, `sink_pmtiles`, `sink_stac`) `COPY`/write to a **local
  temp file**, then upload with **obstore** (`GCSStore` + `obs.put`, ADC auth).
- `sink_ducklake` can't stage locally (DuckLake writes its own chunks during `CREATE
  TABLE`), so on a `gs://` DATA_PATH `catalog.attach()` **skips httpfs** and registers
  **obstore via fsspec** on the connection (`register("gs")` +
  `con.register_filesystem(filesystem("gs"))`). DuckLake honors that fsspec filesystem
  for its DATA_PATH writes (duckdb/ducklake#628) — ADC auth, no HMAC.

All GCS auth is therefore ADC / Workload Identity, identical local and on Cloud Run —
**no gcsfuse, no GCS extension, no HMAC.** If you see a GCS 403, the fix is the fsspec
registration, never re-enabling httpfs.

> Note: DuckLake replaced an earlier Iceberg implementation (commit `d62e423`). Some
> docstrings still say "iceberg" / "sink_iceberg" — those are stale comments, not
> live code. Native DuckDB geom + timestamps; no WKB-cast or tz-normalize hacks.

**PMTiles** (`sink_pmtiles.py`) shells out to the `tippecanoe` binary (built into the
Docker image; not a Python dep) — DuckDB writes GeoJSONSeq to a temp file, tippecanoe
produces the `.pmtiles`, then it uploads to GCS via obstore.

## Environment variables

| Var | Used by | Notes |
|---|---|---|
| `SOURCE_BACKEND` | `ingest.py` | `postgres` (default) or `postgrest` |
| `POSTGRES_DSN` | `source.py` | libpq DSN; local dev via `cloud_sql_proxy` |
| `POSTGREST_URL` / `POSTGREST_PAGE` | `source_postgrest.py` | HTTP bandaid backend |
| `DUCKLAKE_CATALOG_DSN` | `catalog.py` | libpq DSN for catalog Postgres (required) |
| `DUCKLAKE_DATA_PATH` | `catalog.py` | data-chunk path; `gs://…` triggers the obstore-fsspec route |
| `DUCKLAKE_METADATA_SCHEMA` | `catalog.py` | Postgres schema for DuckLake metadata tables (default `ducklake_catalog`) |
| `OVERRIDE_DATA_PATH` | `catalog.py` | `True` adds `OVERRIDE_DATA_PATH TRUE` to ATTACH — use when DATA_PATH differs from what the catalog recorded (e.g. sandbox vs prod) |
| `WAREHOUSE_BUCKET` | `core/config.py` | one GCS bucket for all artifacts (private; served via CDN) |
| `WAREHOUSE_{ARCHIVE,PMTILES,STAC}_PREFIX` | `core/config.py` | per-artifact object prefixes |
| `WAREHOUSE_PUBLIC_BASE_URL` | `core/config.py` | https base for asset/CDN hrefs — the maps-assets CDN (path-preserved; bucket is private). Defaults to `https://maps-assets.geology.utah.gov` |
| `TIPPECANOE_BIN` / `TIPPECANOE_OPTS` | `vector/sink_pmtiles.py` | binary path + extra flags |

All GCS access (obstore for the file sinks, obstore-fsspec for DuckLake) uses ADC, so
the runtime needs `gcloud auth application-default login` (local) or a service account /
Workload Identity (Cloud Run). No HMAC keys, no gcsfuse.

## Conventions

- Python 3.11+, `from __future__ import annotations` at the top of every module.
- All target geometry is **EPSG:4326**; `transform.TARGET_SRS` / `H3_RESOLUTION` are
  the single source of truth.
- dbt convention: geometry column is named `geom`; `_current` is the table suffix.
- Coordinate any change touching the `_current` contract with marshallrobinson — the
  neighbor repos (`dataELT`, `ugs-ingest`, `ugs-map-viewer`) are read-only from here.
