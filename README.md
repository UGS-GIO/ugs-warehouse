# ugs-warehouse

Cloud-native data warehouse for the Utah Geological Survey. Two **producers** sit on a **shared
core** and emit one strict-STAC catalog (+ DuckLake, GeoParquet, PMTiles, COG) to a private GCS
bucket, served read-only through the maps-assets CDN and a static STAC viewer.

- **vector** — Postgres `{schema}.{topic}_current` serving tables → DuckLake table, GeoParquet
  archive, PMTiles, STAC item.
- **pubs** — UGS publications → COG (geologic plates), footprints + unit polygons, cover
  thumbnails, STAC item. See `docs/INTEGRATION_GEOLMAP.md`.

One STAC catalog spans both, laid out with collections (`ugs-serving-topics`, `ugs-publications`,
`ugs-rasters`). An optional Django **ops console** (`admin/`) drives + observes the Cloud Run jobs.

## Architecture

```
vector producer                         pubs producer
  Pub/Sub {schema, topic}                 CSV/MySQL manifest + footprints
   ↓ push → service/main.py                ↓ Cloud Run Job (sharded)
  source → transform (4326·h3·hilbert)    harvest zip→COG · footprints · units · thumbs
   ↓                                       ↓
  └──────────────┬─────────── core/stac.py (one item builder, one catalog) ───────────┘
                 ↓
     gs://…/warehouse/{ducklake,geoparquet,pmtiles,cogs,stac}/   →  maps-assets CDN
                 ↓                                                     ↓
         STAC catalog (collections + items.json index)          viewer/  +  STAC Browser
```

Styling rides the catalog: `ugs-styles` (neighbor repo) → a `ugs:renders` block + `roles:["style"]`
asset per item, rebound by the `restyle` job without a reingest. See `docs/STYLING.md`.

## Cheap-path choices

| Concern | Choice |
|---|---|
| Compute engine | DuckDB embedded (single node — UGS fits) |
| Lakehouse format | DuckLake (native DuckDB geom + tz, simpler than Iceberg at this scale) |
| Catalog DB | DuckLake-on-Postgres in the existing `mapping-db` Cloud SQL (no new database) |
| Data files | parquet/COG/PMTiles on one GCS bucket (private; CDN-fronted) |
| Serving | static STAC on the CDN + OGC API Features (`featureserv`); no app server in the read path |
| Runtime | Cloud Run (vector: service, scale-to-zero; pubs: sharded Jobs) |

No Spark, no Dataproc, no PyIceberg. Iceberg's multi-engine federation buys nothing at UGS scale;
DuckLake handles native geometry + timestamps directly.

## Layout

```
src/ugs_warehouse/
├── core/        shared by both producers
│   ├── config.py    one bucket + CDN base + object prefixes
│   ├── gcs.py       obstore upload/list + Cache-Control + CDN URLs
│   ├── stac.py      item builder, collections hierarchy, derive-from-truth catalog refresh
│   ├── styles.py    ugs-styles bridge → ugs:renders block + style asset
│   └── iso.py        STAC → ISO 19139 sidecars
├── vector/      producer A: Postgres _current → DuckLake/GeoParquet/PMTiles/STAC
│   ├── source.py transform.py ducklake.py sink_archive.py sink_pmtiles.py sink_stac.py
│   ├── related.py   FK relationships → related links + ugs:foreign_keys + aspatial child tables
│   └── ingest.py topics.py
├── pubs/        producer B: publications → COG/footprints/units/thumbs/STAC
│   ├── harvest.py (zip→COG) footprints.py vectors.py units_pmtiles.py thumbs.py
│   └── source.py identity.py sink_stac.py topic.py ingest.py
├── raster/      standalone raster COGs → STAC (consume.py, sink_stac.py)
└── restyle.py   rebind ugs:renders from the manifest, no reingest

service/         Cloud Run service: Pub/Sub push → vector ingest
admin/           Django + HTMX ops console (IAP): run/observe the Cloud Run jobs
featureserv/     duckdb_featureserv config — OGC API Features over the GeoParquet
viewer/          React + MapLibre STAC viewer (static; reads the catalog off the CDN)
scripts/         CLI: bootstrap catalog, manual ingest, provision pub/sub
docs/            MkDocs site (ARCHITECTURE, SERVING, STYLING, USER_GUIDE, …)
```

## Local dev (vector)

Personal `gcloud` auth + the existing `cloud_sql_proxy` to reach `mapping-db`:

```bash
cloud_sql_proxy -instances=ut-dnr-ugs-mappingdb-prod:us-west3:mapping-db=tcp:5433

pip install -e ".[dev]"

export POSTGRES_DSN="host=127.0.0.1 port=5433 dbname=seamlessgeolmap user=$USER password=$PASS"
export DUCKLAKE_CATALOG_DSN="$POSTGRES_DSN"
export DUCKLAKE_DATA_PATH=gs://ut-dnr-ugs-maps-prod-public/warehouse/ducklake/

# dotted form: schema.layer_current
python -m ugs_warehouse.vector.ingest --topic hazards.hazards_qfaults_current
python -m ugs_warehouse.vector.ingest --topic hazards.hazards_qfaults_current --dry-run

# --all discovers every _current in the mart schemas at runtime
python -m ugs_warehouse.vector.ingest --all
```

The pubs producer + ops console have their own entrypoints — see `docs/INTEGRATION_GEOLMAP.md`
and `admin/`. CI + dedicated service accounts come at deploy time (`docs/DEPLOY.md`).
