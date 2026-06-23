# Serving tier — how consumers reach the warehouse

**TL;DR:** the lake serves itself. GeoParquet / COG / PMTiles / STAC live on the bucket and are
served by the **CDN with no running service**. The only running service is one **scale-to-zero OGC
API Features** façade for clients that need a queryable feature *service* (ArcGIS Pro / QGIS / AGOL).
Services are a thin read-through, never the system of record.

## Consumer → access path

| Consumer | Wants | Serve via | Service? |
|---|---|---|---|
| ArcGIS Pro / QGIS / AGOL (vector) | queryable feature layer | **OGC API Features** (`duckdb_featureserv`) | yes — scale-to-zero |
| ArcGIS Pro (raster) | the geologic-map COGs | **STAC catalog** + **COG by URL** | no — CDN |
| Web maps | tiles | **PMTiles** (MapLibre) | no — CDN |
| Analysts / QGIS / Python / R / DuckDB | query + download | **GeoParquet** (range-read pushdown) | no — CDN |
| Discovery | "what exists" | **STAC catalog** | no — CDN |

## The one service: OGC API Features

The access tier is **`duckdb_featureserv` over GeoParquet** (`featureserv/`, Cloud Run service
`ugs-warehouse-features`). Collections are **DuckDB views over the parquet on the CDN** — no database,
no data copy; it reads the lake. Scale-to-zero (`min-instances=0`) → ~$0 idle. ArcGIS Pro, QGIS, and
AGOL all consume OGC API Features, so one service covers them.

!!! note "Parallel path: pg_featureserv (`api/`)"
    A second OGC service exists — CrunchyData **`pg_featureserv`** over the Postgres `_current` tables
    (`ugs-warehouse-api`). Its `config.toml` has **drifted** (`gen_gis` instead of `gengis`, only 5 of
    7 mart schemas). Reconcile it with — or retire it in favour of — the `duckdb_featureserv` tier.

## Why not the alternatives

- **GeoServer** — full WFS/OGC/WMS but always-on (~$600/mo). Opposite of scale-to-zero. Cut.
- **Raw GeoParquet for Pro** — Pro can open it but read-then-filters the whole file (no spatial
  pushdown, no service semantics). Parquet is for the DuckDB/analyst crowd, not Pro.
- **Hosted feature layers in AGOL** — copies data into Esri (duplication + lock-in). Only if a
  workflow genuinely needs browser editing.
- **Koop** (native Esri feature service, also scale-to-zero) — a fine fallback if OGC API Features
  ever falls short in AGOL; not needed today.
