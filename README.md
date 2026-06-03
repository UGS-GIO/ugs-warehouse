# ugs-warehouse

Iceberg lakehouse for UGS. Forks at the published gold contract — the Postgres
`{schema}.{topic}_current` serving tables — and produces Iceberg tables,
GeoParquet archives, PMTiles, and STAC items on GCS.

## Architecture

```
publish.sh (dataELT) → Pub/Sub {schema, topic}
   ↓ push subscription
Cloud Run service (this repo)
   ↓
DuckDB reads {schema}.{topic}_current from Cloud SQL (mapping-db)
   ↓ reproject→4326 · h3_r9 · hilbert-sort · ST_AsWKB
   ├─→ Iceberg table (WKB geom)      catalog: iceberg_catalog schema on mapping-db
   ├─→ GeoParquet archive (native)   gs://.../warehouse/geoparquet/{topic}/
   ├─→ PMTiles (vector, tippecanoe)  gs://.../warehouse/pmtiles/{topic}/
   └─→ STAC item                     gs://.../warehouse/stac/{topic}/
```

## Cheap-path choices

| Concern | Choice |
|---|---|
| Compute engine | DuckDB embedded (single node — UGS fits) |
| Lakehouse format | Iceberg (open multi-engine read; standard) |
| Geometry in Iceberg | **WKB BLOB** — native geo lives in the GeoParquet archive |
| Catalog | PyIceberg SQL catalog in a schema on the existing `mapping-db` Cloud SQL (no new database) |
| Runtime | Cloud Run service, scale-to-zero |
| Trigger | Pub/Sub from `publish.sh` (push subscription) |

No Spark, no Dataproc. SedonaDB single-node only re-enters if native geometry inside Iceberg becomes a hard requirement.

## Layout

```
src/ugs_warehouse/
├── topics.py        topic registry (the {schema}.{topic}_current set)
├── catalog.py       PyIceberg SQL catalog (mapping-db)
├── source.py        Postgres _current reader (Cloud SQL Connector)
├── transform.py     DuckDB reproject + h3 + hilbert + WKB
├── sink_iceberg.py  Iceberg table write (WKB geom)
├── sink_archive.py  GeoParquet archive write (native geom)
├── sink_pmtiles.py  tippecanoe wrapper
├── sink_stac.py     STAC item write
└── ingest.py        per-topic orchestration

service/             Cloud Run service: Pub/Sub push → ingest
scripts/             CLI: bootstrap catalog, manual ingest
```

## Local dev

Personal `gcloud` auth + the existing `cloud_sql_proxy` to reach `mapping-db`:

```bash
cloud_sql_proxy -instances=ut-dnr-ugs-mappingdb-prod:us-west3:mapping-db=tcp:5432

pip install -e ".[dev]"

export ICEBERG_CATALOG_URI=postgresql+psycopg://$USER:$PASS@127.0.0.1:5432/seamlessgeolmap
export ICEBERG_WAREHOUSE_PATH=gs://ut-dnr-ugs-maps-prod-public/warehouse/iceberg/

python -m ugs_warehouse.ingest --topic hazards_qfaults_current
python -m ugs_warehouse.ingest --all
```

CI + dedicated service accounts come at deploy time, not build time.
