# ogcapi — serverless OGC API Features (DuckDB over GeoParquet)

A scale-to-zero [OGC API - Features](https://ogcapi.ogc.org/features/) endpoint over the
warehouse's GeoParquet. **No database.** Collections are derived from the live STAC catalog;
feature queries run DuckDB over the parquet read straight off the public CDN URL (httpfs), so the
service is stateless and needs no GCP credentials. One Cloud Run instance serves every layer — a
cold start (~1–2s, extensions baked into the image) boots them all; switching layers is just a
different path to the same warm instance. Bbox filtering is pushed into DuckDB, not scanned.

Why this over the `api/` pg_featureserv: that one needs a live Cloud SQL connection. This serves
straight from the lake (parquet on object storage) — the cloud-native, truly-serverless path. See
`docs/SERVING.md` / the OGC API notes.

## Endpoints (Features Core)

```
GET /                              landing
GET /conformance
GET /collections                   from the STAC catalog (serving-topics; pubs skipped)
GET /collections/{id}
GET /collections/{id}/items?bbox=minx,miny,maxx,maxy&limit=&offset=   → GeoJSON
GET /collections/{id}/items/{fid}
```

## Env

| var | default | |
|---|---|---|
| `STAC_CATALOG` | prod `catalog.json` | catalog to derive collections from |
| `STAC_SKIP` | `ugs-publications` | collections to ignore (COG plates, no parquet) |
| `GEOM_COLUMN` | `geom` | geometry column in the parquet |

## Local

```bash
pip install -r requirements.txt
python -c "import duckdb;duckdb.connect().execute('INSTALL spatial;INSTALL httpfs;')"  # once
uvicorn main:app --reload --port 8080
# http://localhost:8080/collections
# http://localhost:8080/collections/enmin_ucrc_wells/items?limit=5
```

## Deploy

`cloudbuild.yaml` builds `ogcapi/` → `ugs-ogcapi` image → Cloud Run service `ugs-warehouse-ogcapi`,
`--allow-unauthenticated`, no Cloud SQL, no secrets, scale-to-zero. Map a domain (e.g.
`features.geology.utah.gov`) when ready.

## Notes / limits

- Feature `id` is the row ordinal (parquet is hilbert-sorted → stable). Add a stable id column
  upstream if persistent feature ids are needed.
- Features Core only today (no CQL filtering, no output CRS negotiation — data is already 4326).
- For very large layers, bbox queries benefit from GeoParquet 1.1 bbox covering columns (roadmap);
  DuckDB still filters correctly without them, just scans more.
