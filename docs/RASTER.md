# Raster path

Standalone raster layers (a slope grid, a scanned map edition) reach the catalog
through `src/ugs_warehouse/raster/`. ugs-ingest validates and stages each raster; the warehouse
copies the staged COG to the public bucket and writes a STAC item into the shared catalog.
Publication plates and the geologic-map mosaics are a separate path (the pubs producer).

## Why it looks like this

- **COG is the primary artifact.** It is the raster analog of GeoParquet: an open, cloud-native
  format that GDAL, QGIS, rasterio and web maps read directly with HTTP range requests. There is
  no tile server in the read path; the viewer draws COGs client-side
  (`viewer/src/map/cog.ts`, `@geomatico/maplibre-cog-protocol`).
- **One catalog, separate pipeline.** A raster edition is a STAC item like any vector topic or
  publication, so it goes through the shared `core.stac` builder and catalog refresh. Storage and
  transforms differ (pixels, not features), so the raster code does not extend the vector ingest.
- **ugs-ingest owns processing, the warehouse owns publishing.** GDAL work (COG build, web-mercator
  derivative, thumbnail) happens in ugs-ingest. The warehouse only copies finished files and catalogs them,
  so the warehouse service stays light.

## Flow

```
ugs-ingest: stage COG -> gs://stagedrasters/...  +  row in raw.raster_catalog
dataELT raster promote -> Pub/Sub ugs-warehouse-raster-promote  {"item_id": "..."}
  -> push subscription ugs-warehouse-raster-push -> POST /raster  (service/main.py)
  -> raster.consume.consume(item_id)
       source.fetch_record   SELECT the edition from raw.raster_catalog
       consume.promote       copy COG (+ web-mercator COG, thumbnail) into the public bucket
       sink_stac.write       build item, attach ugs-styles renders, upload item JSON
       core.stac.refresh_catalog()
```

- `POST /raster` (`service/main.py`) takes `{"item_id": ..., "layer": ...}`; `item_id` is
  required. A malformed id returns 400. An `item_id` with no catalog row (not promoted to prod
  yet) is acked and skipped, so Pub/Sub does not retry it.
- `scripts/provision.sh` creates the topic, the push subscription (600 s ack deadline, dead-letter
  topic `ugs-warehouse-raster-dlq`), and a topic-level publisher grant for the dataELT service
  account.
- There is no CLI. For a manual run, call `consume(item_id)` from `ugs_warehouse.raster.consume`.

## Record contract (`raw.raster_catalog` to record)

`source.fetch_record` runs the SELECT on the Postgres side through DuckDB's `postgres_query`, so
`ST_AsGeoJSON` executes in PostGIS. `item_id` must match `[a-z0-9_]+` before it reaches that SQL.

| Record key | Column | Transform |
|---|---|---|
| `layer`, `item_id`, `collection` | same | verbatim (ingest-authored) |
| `datetime` | `publication_date` | ISO 8601 `YYYY-MM-DDT00:00:00Z` |
| `bbox` | `bbox_4326` | JSON `[w, s, e, n]` |
| `geometry` | `footprint_geom` | GeoJSON; null falls back to the bbox polygon |
| `epsg` | `native_crs` (`"EPSG:26912"`) | integer; unparseable omits `proj:code` |
| `staged_cog_uri` | same | must be `gs://` in an allowlisted bucket |
| `title`, `description`, `data_type`, `units`, `ugs_author`, `ugs_pub_type`, `pub_id`, `is_mosaic`, `has_thumbnail` | same | verbatim |

Editions are append-only: ingest writes one row per edition and `is_current` marks the live one.
The warehouse fetches by exact `item_id`, so every edition gets its own dated COG and item.
`Raster` (`identity.py`) rejects a `layer` or `item_id` that is not a bare `\w` token and a
`collection` path with `..` or empty segments, since all three become GCS object paths.

## Promote

`consume.promote` copies with a server-side GCS rewrite (`core.gcs.copy_from_uri`), so no bytes
pass through the service's memory. Copies get `Cache-Control: immutable`.

| Source (staged) | Destination (public bucket) | Required |
|---|---|---|
| `<name>.cog.tif` | `cog/<layer>/<item_id>.cog.tif` | yes |
| `<name>_3857.cog.tif` | `cog/<layer>/<item_id>_3857.cog.tif` | no; skipped if absent |
| `<name>.thumb.png` | `cog/<layer>/<item_id>.thumb.png` | only when `has_thumbnail` |

The source bucket must be in `WAREHOUSE_STAGED_SOURCE_BUCKETS`: `staged_cog_uri` is a catalog
value, and whatever it names ends up on the CDN.

## STAC item

- Path: `warehouse/stac/ugs-rasters/<layer>/<item_id>/<item_id>.json`. The record's
  `collection` is the layout path (`ugs-rasters/<layer>`); the STAC `collection` id is its last
  segment (`<layer>`).
- `properties`: `datetime`, `title`, `description`, `data_type`, `units`, `ugs:author`,
  `ugs:pub_type`, `ugs:pub_id`, `ugs:is_mosaic`. Empty values are dropped. A `data_type` outside
  the STAC pixel-type enum (e.g. `categorical`) is published as `other`, with the original on
  `ugs:data_type`, so the item still validates.
- `proj:code` (projection extension) from `epsg`; `file:size` on each copied asset.
- `ugs:renders` and a `style` asset when ugs-styles has an entry for the item id.

| Asset key | Href | Roles | Present when |
|---|---|---|---|
| `cog` | native-CRS COG | `data` | always |
| `visual` | EPSG:3857 COG | `visual` | the web-mercator copy landed |
| `thumbnail` | PNG | `thumbnail` | `has_thumbnail` |

The native COG is `data` only. A client that takes `visual` literally tries to draw it on a
web-mercator map, which fails for a UTM raster, so `visual` is reserved for the reprojected copy.
The COG is advertised as an asset, not a web-map-links `cog` link; that extension defines no such
rel.

On refresh, each `ugs-rasters/<layer>` collection also gets a `thumbnail` asset (from its newest
item with one). Every collection, raster or not, gets an `items` asset: `items.parquet`, a
stac-geoparquet mirror of its items that rustac writes (`core/item_mirror.py`).

## Environment

| Variable | Default | Used by |
|---|---|---|
| `POSTGRES_DSN`, `PGPASSWORD` | local proxy DSN | `source.py` |
| `WAREHOUSE_STAGED_SOURCE_BUCKETS` | `stagedrasters` | `consume._staged_source` |
| `WAREHOUSE_RASTER_COG_PREFIX` | `cog` | `identity.py` object paths |
| `WAREHOUSE_BUCKET`, `WAREHOUSE_PUBLIC_BASE_URL`, `WAREHOUSE_STAC_PREFIX` | see `core/config.py` | shared |

The runtime service account needs read on the staged bucket.

## Not built

- Time-series datacubes (Zarr / Icechunk): no warehouse code writes one. The viewer can read a
  STAC zarr asset (`viewer/src/zarr/store.ts`) if a producer adds it.
- RaQuet (raster in Parquet for DuckDB queries) and a dynamic tile server (titiler).

Tests: `tests/test_raster.py`.
