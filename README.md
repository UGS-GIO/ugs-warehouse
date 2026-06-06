# ugs-warehouse

DuckLake lakehouse for UGS. Forks at the published gold contract — the Postgres
`{schema}.{topic}_current` serving tables — and produces DuckLake tables,
GeoParquet archives, PMTiles, and STAC items on GCS.

## Architecture

```
publish.sh (dataELT) → Pub/Sub {schema, topic}
   ↓ push subscription
Cloud Run service (this repo)
   ↓
DuckDB reads {schema}.{topic}_current from Cloud SQL (mapping-db)
   ↓ confirm/reproject → 4326 · h3_r9 · hilbert-sort
   ├─→ DuckLake table (native geom)  catalog: Postgres on mapping-db
   ├─→ GeoParquet archive (native)   gs://.../warehouse/geoparquet/{topic}/
   ├─→ PMTiles (vector, tippecanoe)  gs://.../warehouse/pmtiles/{topic}/
   └─→ STAC item                     gs://.../warehouse/stac/{topic}/
```

## Cheap-path choices

| Concern | Choice |
|---|---|
| Compute engine | DuckDB embedded (single node — UGS fits) |
| Lakehouse format | DuckLake (native DuckDB geom + tz, simpler than Iceberg at this scale) |
| Catalog | DuckLake-on-Postgres in the existing `mapping-db` Cloud SQL (no new database) |
| Data files | parquet chunks on GCS |
| Runtime | Cloud Run service, scale-to-zero |
| Trigger | Pub/Sub from `publish.sh` (push subscription) |

No Spark, no Dataproc, no PyIceberg. Iceberg's multi-engine federation buys nothing at UGS scale; DuckLake handles native geometry + timestamps directly.

## Layout

```
src/ugs_warehouse/
├── topics.py         Topic primitives + runtime discover() (no hard-coded list)
├── catalog.py        DuckLake ATTACH on mapping-db Postgres
├── source.py         Postgres _current reader (Cloud SQL Connector)
├── transform.py      DuckDB reproject + h3 + hilbert
├── sink_ducklake.py  DuckLake table write (native geom)
├── sink_archive.py   GeoParquet archive write (native geom)
├── sink_pmtiles.py   tippecanoe wrapper
├── sink_stac.py      STAC item write
└── ingest.py         per-topic orchestration

service/              Cloud Run service: Pub/Sub push → ingest
scripts/              CLI: bootstrap catalog, manual ingest
```

## Source backend (`SOURCE_BACKEND` env)

The source layer is swappable:

| Value | Reads from | When |
|---|---|---|
| `postgres` (default) | direct libpq via DuckDB postgres extension | prod; whenever you have a Postgres login on mapping-db |
| `postgrest` | HTTP via `postgrest-seamlessgeolmap` Cloud Run | **bandaid** — lets the warehouse run without a Postgres login. `web_anon` has `SELECT` on emp / wetlands / mapping schemas (hazards + gen_gis return 401). Slower (HTTP + GeoJSON). See `src/ugs_warehouse/source_postgrest.py` for caveats |

Switch back to `postgres` once direct DB creds land — only `source.py` changes; transform + sinks unaffected.

## Local dev

Personal `gcloud` auth + the existing `cloud_sql_proxy` to reach `mapping-db`:

```bash
cloud_sql_proxy -instances=ut-dnr-ugs-mappingdb-prod:us-west3:mapping-db=tcp:5432

pip install -e ".[dev]"

export POSTGRES_DSN="host=127.0.0.1 port=5432 dbname=seamlessgeolmap user=$USER password=$PASS"
export DUCKLAKE_CATALOG_DSN="$POSTGRES_DSN"
export DUCKLAKE_DATA_PATH=gs://ut-dnr-ugs-maps-prod-public/warehouse/ducklake/

# dotted form: schema.layer_current
python -m ugs_warehouse.ingest --topic hazards.hazards_qfaults_current
python -m ugs_warehouse.ingest --topic hazards.hazards_qfaults_current --dry-run

# --all discovers every _current in the mart schemas at runtime
python -m ugs_warehouse.ingest --all
```

CI + dedicated service accounts come at deploy time, not build time.
