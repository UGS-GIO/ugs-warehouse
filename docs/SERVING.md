# Serving tier — how consumers reach the warehouse

**Principle:** the lake is canonical and serves itself. Cloud-native artifacts
(GeoParquet / COG / PMTiles / STAC) live on the bucket and are served by the **CDN with
no running service**. On top of that we add **one thin, scale-to-zero façade** for the
consumers that need a queryable feature *service* (ArcGIS Pro / AGOL). Services are a
serving tier, never the system of record.

## Consumer → access path

| Consumer | Wants | Serve via | Running service? |
|---|---|---|---|
| **ArcGIS Pro** (vector) | queryable feature layer | **OGC API Features** (pg_featureserv) | yes — scale-to-zero |
| **ArcGIS Online** (vector) | feature layer by URL | **OGC API Features** (pg_featureserv) — Esri ingests OGC | yes — scale-to-zero |
| **ArcGIS Pro** (raster) | the geologic-map COGs | **STAC catalog** (Pro 3.1+ native) + **COG by URL** | **no** — CDN |
| **AGOL** (raster) | imagery | **COG** referenced / titiler WMTS (later) | no / optional |
| **Web maps** | tiles | **PMTiles** (MapLibre) | **no** — CDN |
| **Analysts / QGIS / Python** | partial query, download | **GeoParquet** (range-read pushdown), STAC | **no** — CDN |
| **Discovery** | "what exists" | **STAC catalog** | **no** — CDN |

## The one service: pg_featureserv (OGC API Features)

- **Why:** ArcGIS Pro *and* ArcGIS Online both ingest **OGC API Features**. One tiny Go
  service over the Postgres `{schema}.{layer}_current` serving tables gives both products
  a real, server-side-queryable feature layer (indexed spatial query — unlike a raw
  parquet, which Pro can only read-then-filter, no pushdown).
- **Cost:** Cloud Run **min-instances=0** → ~$0 idle, pay-per-request. (vs GeoServer
  WFS/OGC at **~$600/mo always-on** — killed for that reason.)
- **No data duplication** — reads the serving tables live (vs publishing hosted layers
  *into* AGOL, which copies data + is Esri lock-in).

### Deploy sketch (later — not built yet)
- Container: Crunchy Data `pg_featureserv` (Go).
- **Read-only DB role** scoped to the serving schemas (reuse `schema_reader` once it lands,
  or a dedicated `featureserv_ro`). Never the write/owner role.
- Cloud Run: `--set-cloudsql-instances` (socket), `DATABASE_URL` from Secret Manager,
  `min-instances=0`, public ingress, **CORS enabled** (AGOL adds layers from a browser →
  needs CORS; Pro desktop doesn't).
- Config: expose the mart serving schemas; pg_featureserv auto-publishes tables the role
  can see. No per-layer config.

## Why not the alternatives

- **GeoServer** — does WFS + OGC + WMS, but **~$600/mo always-on**. Opposite of the
  scale-to-zero model. Cut.
- **Raw GeoParquet for Pro** — Pro can *open* GeoParquet (3.3+) and filter a layer, but it
  **read-then-filters the whole file, no pushdown/spatial index** — slow + clunky against a
  bucket, no service semantics, no editing. Parquet is for the DuckDB/analyst crowd, not Pro.
- **Hosted feature layers in AGOL** — native, but **copies data into Esri** (duplication +
  lock-in + credits). Only if a workflow genuinely needs browser editing.
- **Koop** (Esri GeoServices via Node) — would give a *native* ArcGIS feature service, also
  scale-to-zero; a fine alternative if OGC API Features ever falls short in AGOL. For now
  OGC API Features (pg_featureserv) covers both Pro + AGOL, so it's the simpler pick.

## Bottom line

CDN serves the canonical artifacts (no service, near-free). **pg_featureserv** is the
single scale-to-zero façade that makes the vector layers queryable in ArcGIS Pro + AGOL.
Match the surface to the consumer; keep services thin read-throughs over the lake.
