# Serving tier — how consumers reach the warehouse

**TL;DR:** the lake serves itself. GeoParquet / COG / PMTiles / STAC live on the bucket and are
served by the **CDN with no running service**. The only running service is one **scale-to-zero OGC
API Features** façade for clients that need a queryable feature *service* (ArcGIS Pro / QGIS / AGOL).
Services are a thin read-through, never the system of record.

## Consumer → access path

| Consumer | Wants | Serve via | Service? |
|---|---|---|---|
| ArcGIS Pro / QGIS / AGOL (vector) | queryable feature layer | **OGC API Features** (pygeoapi) | yes — scale-to-zero |
| AGOL Map Viewer / Experience Builder / StoryMaps | styled feature layer | **ArcGIS FeatureServer** (Go, [geoparquet-featureserver](https://github.com/UGS-GIO/geoparquet-featureserver)) | yes — scale-to-zero |
| ArcGIS Pro (raster) | the geologic-map COGs | **STAC catalog** + **COG by URL** | no — CDN |
| Web maps | tiles | **PMTiles** (MapLibre) | no — CDN |
| Analysts / QGIS / Python / R / DuckDB | query + download | **GeoParquet** (range-read pushdown) | no — CDN |
| Discovery | "what exists" | **STAC catalog** | no — CDN |

## The one service: OGC API Features

The access tier is **pygeoapi over GeoParquet** (`featureserv/`, Cloud Run service
`ugs-warehouse-features`). Each collection reads its GeoParquet from the bucket. There is no
database and no data copy. The layer list comes from the catalog refresh. Scale-to-zero (`min-instances=0`) → ~$0 idle. ArcGIS Pro, QGIS, and
AGOL all consume OGC API Features, so one service covers them.

!!! note "Parallel path: pg_featureserv (`api/`)"
    A second OGC service exists — CrunchyData **`pg_featureserv`** over the Postgres `_current` tables
    (`ugs-warehouse-api`). Its `config.toml` has **drifted** (`gen_gis` instead of `gengis`, only 5 of
    7 mart schemas). Reconcile it with — or retire it in favour of — the pygeoapi tier.

## Why not the alternatives

- **GeoServer** — full WFS/OGC/WMS but always-on (~$600/mo). Opposite of scale-to-zero. Cut.
- **Raw GeoParquet for Pro** — Pro can open it but read-then-filters the whole file (no spatial
  pushdown, no service semantics). Parquet is for the DuckDB/analyst crowd, not Pro.
- **Hosted feature layers in AGOL** — copies data into Esri (duplication + lock-in). Only if a
  workflow genuinely needs browser editing.
- **Koop** (native Esri feature service on Node) — served the FeatureServer first, then ran out of
  memory at 4 GiB on the heavy layers and could not hold 5 requests a second under a 1 s p99. The
  FeatureServer is now a Go binary with Koop's responses, beside pygeoapi, because AGOL draws OGC
  API Features in one default symbol; a FeatureServer carries its renderer.

## `ugs:summary_fields` — which columns identify a row

A serving topic can carry 20+ columns, most of them plumbing (`ogc_fid`, `target_epsg`,
`_publication_date`). A consumer that can't show them all — the viewer's record cards on a phone —
has to pick, and picking by column order is how the CCUS regions ended up showing the same number
three times before the basin's ranking.

So the item can name them:

```json
"properties": {
  "ugs:summary_fields": ["basn_nm", "ranking", "resrvrs"]
}
```

Ordered, most identifying first. Consumers lead with these and fall back to a heuristic (skip
system-looking names and columns that never vary) when the item declares none — so this is an
improvement to opt into, never a requirement.

**Source:** `summary_fields` on `raw.schema_registry`, alongside the other curated catalog metadata
(`iso_topic_category`, `lineage`, …). `vector/sink_stac.py` already copies it through; the column
does not exist on the registry yet, so `vector/source.py` does not select it — **ugs-ingest owns
that DDL**, and until it lands every item simply has no `ugs:summary_fields`.
