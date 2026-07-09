# AGENTS.md

Single agent-instructions file for this repo (`AGENTS.md` standard). If tool default to other name (Claude Code → `CLAUDE.md`, Gemini → `GEMINI.md`), point here via tool config instead of duplicating.

## What this is

DuckLake lakehouse for UGS. Fork at dataELT published gold contract — Postgres `{schema}.{layer}_current` serving tables — emit 4 artifacts per topic: DuckLake table (native geom), GeoParquet archive, PMTiles, STAC item. No re-derive silver/gold. Add serving shape on top of existing dbt mart contract owned upstream (marshallrobinson). See `docs/HANDOFF.md` for live working state, blockers, decision history (not committed).

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

`--dry-run` exercise source + transform only (no GCS/catalog writes). Safe smoke against real `_current` data. Print row count, 4326 bbox, sample row. Topic with **0 rows carrying geometry** report `SKIP` (rc=1), never reach sinks (see geometry guard).

**Tests:** `pytest` over `tests/` — hermetic unit tests (in-memory GCS + fake manifests/DB, no network) cover STAC builders, styles/restyle, related-FK emit, raster + pubs sinks, ISO, topics. Run `python -m pytest tests/ -q`. Use `ruff` + `--dry-run` for live smoke.

## Layout

Two **producers** on **shared core**:
- `core/` — `config` (one `WAREHOUSE_BUCKET` + CDN `PUBLIC_BASE_URL` + prefixes), `gcs` (obstore upload/list + Cache-Control + CDN URLs), `stac` (item builder, collections hierarchy, derive-from-truth catalog refresh, web-map-links, titles). **Both producers emit through this.**
- `vector/` — producer A: Postgres `_current` topics → DuckLake + GeoParquet + PMTiles + STAC.
- `pubs/` — producer B: publications → COG + footprints + units + cover thumbnails + STAC. See `docs/INTEGRATION_GEOLMAP.md`.
- `raster/` — standalone COG → STAC (`ugs-rasters` collection).
- `restyle.py` — rebind `ugs:renders` from ugs-styles manifest, no reingest (`docs/STYLING.md`).

Outside `src/`: `admin/` (Django + HTMX ops console behind IAP — drive + observe Cloud Run jobs), `service/` (Pub/Sub push handler), `featureserv/` (OGC API Features), `viewer/` (STAC viewer). One STAC catalog span producers — collections `ugs-serving-topics`, `ugs-publications`, `ugs-rasters`.

## Architecture (vector producer)

Pipeline: `source → transform → 4 sinks`, orchestrate per topic in `vector/ingest.py`:

```
Pub/Sub {schema, topic} → service/main.py → ingest.ingest_topic(Topic)
   source.read(topic)            → pyarrow Table (geom as geom_wkb BLOB + target_epsg)
   transform.run(arrow)          → DuckDB conn + "transformed" view (4326, h3_r9, hilbert-sorted)
   sink_ducklake / sink_archive / sink_pmtiles / sink_stac  (each reads the view)
```

**Data contract between layers — key invariant.** `source.read()` return pyarrow Table where geom is `geom_wkb` BLOB (server-side `ST_AsBinary`), each row carry `target_epsg` (CRS WKB bytes are in) and `source_epsg` (upstream provenance only). `transform.run()` hydrate `geom_wkb` to DuckDB `GEOMETRY`, reproject `target_epsg → 4326` only if not 4326, add `h3_r9` (H3 res 9 cell from centroid), `ORDER BY ST_Hilbert(centroid)` for parquet row-group pruning. New source backend MUST emit same shape.

**Source backend.** `vector/source.py` only — direct libpq via DuckDB postgres extension (`schema_reader` login on mapping-db). Expose `read(topic)` + `discover()`; transform + sinks independent. (HTTP-PostgREST `source_postgrest.py` removed — ignore stale comments).

**Geometry guard.** `ingest._ingest()` check geometry right after `transform.run()`, before sink. If 0 rows non-null `geom`, log `SKIP`, return rc=1 in dry-run and real mode. Catch non-spatial tables, sinks never emit empty/broken PMTiles or null-geom parquet.

**Topics not hard-coded.** `Topic` is `{schema, layer}` (`topics.py`). No registry — `discover()` scan `MART_SCHEMAS` at runtime, `Topic.parse()` handle dotted CLI form, `from_pubsub()` build from trigger payload. Upstream `_current` zero-config.

**Sinks independent and isolated.** `ingest._ingest()` run each sink in own try/except. One fail log to stderr, set `rc=1`, never crash others. Cloud Run handler ack Pub/Sub on `rc!=0` to avoid retry storms (recovery next publish).

**STAC catalog auto-refreshes.** Producer stac sink emit item via `core.stac.write_item()`. Orchestrator call `core.stac.refresh_catalog()`, list item files in GCS, rewrite root `catalog.json` + every `collection.json` (keep current without manual regen, `scripts/refresh_stac.py` on-demand). Derive-from-truth, concurrent ingests converge (last writer win, self-heal). Pure builders in `core/stac.py` (`build_item`, `_group_items`, `_collection_doc`, `_root_doc`), keep pure for tests.

**Rasters** (`raster/`) — standalone COG → STAC pipeline (`consume.py` + `sink_stac.py`) share STAC catalog + GCS + obstore, land items in `ugs-rasters` collection. See `docs/RASTER.md`.

**DuckLake catalog** (`vector/ducklake.py`): catalog metadata in DuckLake-managed Postgres DB; parquet chunks in GCS under `DUCKLAKE_DATA_PATH`. `attach()` idempotent, load `spatial/postgres/ducklake` extensions, pin DuckLake metadata to `METADATA_SCHEMA` (`DUCKLAKE_METADATA_SCHEMA` env, default `ducklake_catalog`) so never land in `public`. `sink_ducklake` run `CREATE OR REPLACE TABLE` per ingest — DuckLake snapshot model keep prior versions readable by id.

**GCS IO — do NOT re-add httpfs for GCS.** DuckDB `httpfs` block S3-compat API with HMAC keys (org policy block). No GCS write through httpfs:
- File sinks (`sink_archive`, `sink_pmtiles`, `sink_stac`) write to **local temp file**, upload with **obstore** (`GCSStore` + `obs.put`, ADC auth).
- `sink_ducklake` write chunk directly — on `gs://` DATA_PATH `catalog.attach()` **skip httpfs**, register **obstore via fsspec** (`register("gs")` + `con.register_filesystem(filesystem("gs"))`). DuckLake honor fsspec for DATA_PATH write, ADC auth, no HMAC.

GCS auth is ADC / Workload Identity, same local and Cloud Run — **no gcsfuse, no GCS extension, no HMAC.** GCS 403 fix is fsspec registration, never httpfs.

> Note: DuckLake replaced Iceberg (commit `d62e423`). Stale iceberg docstrings ignore. Native DuckDB geom + timestamps; no WKB-cast / tz-normalize hacks.

**PMTiles** (`sink_pmtiles.py`) shell out to `tippecanoe` binary (in Docker image). DuckDB write GeoJSONSeq to temp, tippecanoe output `.pmtiles`, upload to GCS via obstore.

## Environment variables

| Var | Used by | Notes |
|---|---|---|
| `POSTGRES_DSN` | `vector/source.py` | libpq DSN; local dev via `cloud_sql_proxy` |
| `DUCKLAKE_CATALOG_DSN` | `vector/ducklake.py` | libpq DSN for catalog Postgres (required) |
| `DUCKLAKE_DATA_PATH` | `vector/ducklake.py` | data-chunk path; `gs://…` trigger obstore-fsspec route |
| `DUCKLAKE_METADATA_SCHEMA` | `vector/ducklake.py` | Postgres schema for DuckLake metadata tables (default `ducklake_catalog`) |
| `OVERRIDE_DATA_PATH` | `vector/ducklake.py` | `True` add `OVERRIDE_DATA_PATH TRUE` to ATTACH — use when DATA_PATH differ from catalog record |
| `WAREHOUSE_BUCKET` | `core/config.py` | GCS bucket for artifacts (private; served via CDN) |
| `WAREHOUSE_{ARCHIVE,PMTILES,STAC}_PREFIX` | `core/config.py` | per-artifact object prefixes |
| `WAREHOUSE_PUBLIC_BASE_URL` | `core/config.py` | https base for asset/CDN hrefs — maps-assets CDN (private bucket, defaults to `https://maps-assets.geology.utah.gov`) |
| `TIPPECANOE_BIN` / `TIPPECANOE_OPTS` | `vector/sink_pmtiles.py` | binary path + extra flags |

GCS access (obstore, obstore-fsspec for DuckLake) use ADC. Runtime need `gcloud auth application-default login` (local) or service account / Workload Identity (Cloud Run). No HMAC, no gcsfuse.

## Querying publications (pubs-duckdb MCP)

Dev/consumer tool, not part of the build. `pubs-duckdb` MCP server (MotherDuck DuckDB MCP, installed in `.venv`) queries the public pub-search `.duckdb` + `corpus.json` on the CDN via SQL/BM25 — no GCP perms (CDN objects are public). Claude Code wires it in `.mcp.json` (gitignored); OpenCode in `opencode.json` (see `opencode.json.example`). Tool = `execute_query` (arg `sql`).

**Bootstrap each session** (in-memory DB resets on restart; full script `scripts/pubs_mcp_bootstrap.sql`). Run statements as SEPARATE `execute_query` calls — the MCP chokes on batched DDL+SELECT:

```sql
INSTALL httpfs; LOAD httpfs; INSTALL fts; LOAD fts;
ATTACH 'https://maps-assets.geology.utah.gov/pubs/search/pubs-fts.duckdb' AS pubs (READ_ONLY);
USE memory;
CREATE OR REPLACE TABLE arts AS SELECT id, sid, title, topic, page, pdf, text
  FROM read_json_auto('https://maps-assets.geology.utah.gov/pubs/search/corpus.json');
PRAGMA create_fts_index('arts','id','title','text', overwrite=1);
```

Two grains:
- `pubs.docs(id,title,series,year,pdf,body)` — every pub (5597), `body` = full document text.
- `arts(id,sid,title,topic,page,pdf,text)` — Survey Notes split to article/page (1052), topic-tagged.

**Query templates** (copy, don't reinvent):
```sql
-- doc-level BM25 — USE pubs first (macro internals unqualified; fails from memory db)
USE pubs;
SELECT id, series, title FROM docs
WHERE fts_main_docs.match_bm25(id,'landslide slope failure') IS NOT NULL
ORDER BY fts_main_docs.match_bm25(id,'landslide slope failure') DESC LIMIT 20;

-- article-level BM25 — from memory db, returns page + topic
SELECT id, sid, page, topic, title FROM arts
WHERE fts_main_arts.match_bm25(id,'wildfire fire burn') IS NOT NULL
ORDER BY fts_main_arts.match_bm25(id,'wildfire fire burn') DESC LIMIT 20;
```

Gotchas: `USE pubs` before doc-level BM25 (else `pubs.fts_main_docs...` breaks on `fts_main_docs.terms`). `execute_query` output caps at 50KB → read big `body` in windows: `SELECT substr(body,1,34000) FROM docs WHERE id='RI-232';`. Semantic/VSS (`pubs-vss.duckdb`) NOT usable here — needs the query embedded (bge-small) first; that stays a viewer job. The `/graph/*.parquet` folder is 404/dead — ignore it and any non-warehouse CDN object.

## Conventions

- Python 3.11+, `from __future__ import annotations` top of module.
- Target geometry is **EPSG:4326**; `transform.TARGET_SRS` / `H3_RESOLUTION` single source of truth.
- dbt convention: geometry column named `geom`; `_current` is table suffix.
- Coordinate change touching `_current` contract with marshallrobinson — neighbor repos (`dataELT`, `ugs-ingest`, `ugs-map-viewer`) read-only here.