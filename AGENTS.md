# AGENTS.md

Single agent-instructions file for this repo (`AGENTS.md` standard). If a tool defaults to another name (Claude Code → `CLAUDE.md`), point it here via tool config instead of duplicating. `GEMINI.md` and `.gemini/styleguide.md` are the PR-review rubric, not agent instructions.

## What this is

Cloud-native warehouse for UGS. The vector producer forks at dataELT's published gold contract (Postgres `{schema}.{layer}_current` serving tables) and emits 4 artifacts per topic: DuckLake table (native geom), GeoParquet archive, PMTiles, STAC item. It never re-derives silver/gold. The pubs and raster producers share the same core and emit into the same one STAC catalog. Architecture: `docs/ARCHITECTURE.md`.

## Commands

```bash
pip install -e ".[dev]"          # editable install + pytest/ruff

ruff check src service scripts    # lint (line-length 100)
python -m pytest tests/ -q        # hermetic: in-memory GCS + fake manifests/DB, no network

# Manual ingest — dotted topic form schema.layer_current
python -m ugs_warehouse.vector.ingest --topic hazards.hazards_qfaults_current
python -m ugs_warehouse.vector.ingest --topic emp.geothermal_kgra_current --dry-run
python -m ugs_warehouse.vector.ingest --all      # runtime-discover every _current in MART_SCHEMAS

python -m scripts.bootstrap_catalog        # idempotent: ATTACH DuckLake + create schemas

# Cloud Run service (Pub/Sub push handler) locally
uvicorn service.main:app --host 0.0.0.0 --port 8080
```

`--dry-run` runs source + transform only (no GCS/catalog writes): a safe smoke test against real `_current` data. A topic with **0 rows carrying geometry** reports `SKIP` (rc=1) and never reaches the sinks.

## Invariants

- **Both producers emit through `core/`** (`config`, `gcs`, `stac`). One item builder, one catalog.
- **Source → transform contract.** `source.read()` returns a pyarrow Table where geom is `geom_wkb` BLOB (server-side `ST_AsBinary`) and each row carries `target_epsg` (the CRS the WKB is in) and `source_epsg` (upstream provenance only). `transform.run()` hydrates `geom_wkb` to DuckDB `GEOMETRY`, reprojects `target_epsg → 4326` only if needed, adds `h3_r9`, and sorts by `ST_Hilbert(centroid)` for row-group pruning. A new source backend MUST emit the same shape.
- **Topics are not hard-coded.** `Topic` is `{schema, layer}` (`topics.py`); `discover()` scans `MART_SCHEMAS` at runtime. A new upstream `_current` needs zero config here.
- **Sinks are isolated.** Each sink runs in its own try/except; one failure logs, sets `rc=1`, and never stops the others. The Cloud Run handler acks Pub/Sub even on `rc!=0` to avoid retry storms; the next publish recovers.
- **The catalog derives from truth.** Sinks write items via `core.stac.write_item()`; `core.stac.refresh_catalog()` rebuilds root `catalog.json` + every `collection.json` from what's in GCS. Never hand-edit catalog JSON. Keep the pure builders in `core/stac.py` (`build_item`, `_group_items`, `_collection_doc`, `_root_doc`) side-effect free.
- **DuckLake metadata is pinned** to `DUCKLAKE_METADATA_SCHEMA` (default `ducklake_catalog`) so it never lands in `public`. `sink_ducklake` does `CREATE OR REPLACE TABLE`; DuckLake snapshots keep prior versions readable.
- **Do NOT add httpfs for GCS.** Org policy blocks the HMAC keys httpfs needs. File sinks write a local temp file and upload with **obstore** (ADC). `sink_ducklake` on a `gs://` data path registers **obstore via fsspec** instead. A GCS 403 is fixed by the fsspec registration, never by httpfs. No gcsfuse, no GCS extension, no HMAC.
- **PMTiles** come from the `tippecanoe` binary in the Docker image (DuckDB writes GeoJSONSeq to a temp file).

## Environment variables

| Var | Used by | Notes |
|---|---|---|
| `POSTGRES_DSN` | `vector/source.py` | libpq DSN; local dev via `cloud_sql_proxy` |
| `DUCKLAKE_CATALOG_DSN` | `vector/ducklake.py` | libpq DSN for catalog Postgres (required) |
| `DUCKLAKE_DATA_PATH` | `vector/ducklake.py` | data-chunk path; `gs://…` triggers the obstore-fsspec route |
| `DUCKLAKE_METADATA_SCHEMA` | `vector/ducklake.py` | Postgres schema for DuckLake metadata tables (default `ducklake_catalog`) |
| `OVERRIDE_DATA_PATH` | `vector/ducklake.py` | `True` adds `OVERRIDE_DATA_PATH TRUE` to ATTACH, for when DATA_PATH differs from the catalog record |
| `WAREHOUSE_BUCKET` | `core/config.py` | GCS bucket for artifacts (private; served via CDN) |
| `WAREHOUSE_SOURCE_BUCKET` | `core/config.py` | Where a producer reads existing artifacts (COGs, footprints) when writing elsewhere, e.g. a review mosaic bake; defaults to `WAREHOUSE_BUCKET` |
| `WAREHOUSE_{ARCHIVE,PMTILES,STAC}_PREFIX` | `core/config.py` | per-artifact object prefixes |
| `WAREHOUSE_PUBLIC_BASE_URL` | `core/config.py` | https base for asset hrefs (defaults to `https://maps-assets.geology.utah.gov`) |
| `TIPPECANOE_BIN` / `TIPPECANOE_OPTS` | `vector/sink_pmtiles.py` | binary path + extra flags |

GCS auth is ADC: `gcloud auth application-default login` locally, the service account / Workload Identity on Cloud Run.

## Conventions

- Python 3.11+, `from __future__ import annotations` at the top of each module.
- Target geometry is **EPSG:4326**; `transform.TARGET_SRS` / `H3_RESOLUTION` are the single source of truth.
- dbt convention: the geometry column is `geom`; `_current` is the table suffix.
- A change touching the `_current` contract is coordinated with dataELT's owner; neighbor repos (`dataELT`, `ugs-ingest`, `ugs-map-viewer`) are read-only from here.
- **Don't commit plans, specs, or handoff notes.** Design and progress go in the PR description or the Jira ticket; git history keeps them. Docs in `docs/` describe the system as it is and change in the same PR as the code they describe. A lasting decision gets a line in the relevant doc, not a new file.
- Querying the publications corpus with the `pubs-duckdb` MCP: `docs/QUERYING_PUBS.md`.
