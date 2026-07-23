# Specification — Cloud-Native Raster Pipeline (`ugs_warehouse/raster`)

This specification defines the architecture, storage standards, time-series schema, and client-side conversion strategies for the UGS Warehouse Raster Pipeline, reconciling one-off interactive ingestion with automated publication harvesting.

---

## 1. Core Principles & Philosophy

The raster pipeline is designed to be **uncompromisingly cloud-native, serverless, and low-maintenance**.
- **No Heavy Server-Side Compute for Delivery**: Avoid running heavy server-side formatting/transformation microservices for user downloads. Leverage client-side execution (WebAssembly/JS) and raw cloud-native object-range reads.
- **Single Source of Truth**: One primary cloud-optimized artifact on GCS serves both direct high-performance analytical queries and interactive web visualization.
- **Standardized Discovery**: Share the same STAC (SpatioTemporal Asset Catalog) discovery substrate as the vector pipeline, providing a single query interface for all spatial and non-spatial datasets.

### 1.1 Dual-Track Architecture

Raster data enters the Warehouse via two independent, loose-coupled tracks that share GCS schemas and target standard COG outputs:

```
┌────────────────────────────────────────────────────────────────────────┐
│ TRACK A: One-off / Interactive Raster Ingest (ugs-ingest)              │
│ UI Upload ──► gs://stagedrasters ──► raster-ingest-processor (CR)      │
│                                           │ (Upsert metadata + STAC)   │
│                                           ▼                            │
│                                   raw.raster_catalog ──► dataELT Promote│
└────────────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────────────┐
│ TRACK B: Automated Publication Batch-Harvest (ugs-warehouse)           │
│ DB metadata / manifests ──► geolmap-harvest Cloud Run Job             │
│                                  │ (Harvests vector GIS / rasters)     │
│                                  ▼                                     │
│                            Public GCS Bucket (cog/ + stac/)            │
└────────────────────────────────────────────────────────────────────────┘
```

1. **Track A: One-off / Interactive Raster Ingestion (`ugs-ingest/raster-ingest-processor`)**
   - **Trigger**: Interactive UI upload (event-driven).
   - **Purpose**: Let operators upload custom GeoTIFF/PDF/GeoPDF maps directly, recover sidecar spatial reference info, and ingest them as first-class cataloged rasters.
   - **Execution**: Managed by a dedicated Python Cloud Run service (`raster-ingest-processor`) that processes the incoming files in-process using GDAL, registers metadata in `raw.raster_catalog` (dev status), and triggers GitHub validation/promotion.

2. **Track B: Automated Publication Batch-Harvest (`ugs-warehouse/geolmap-harvest`)**
   - **Trigger**: Programmatic triggering of the `geolmap-harvest` Cloud Run Job.
   - **Purpose**: Harvest the entire Utah geological publications archive, extracting spatial GIS vector bundles, footprints, and associated rasters.
   - **Execution**: Managed by `ugs-warehouse`'s pubs pipeline, compiling footprints to `.pmtiles` and uploading COGs/STAC directly.

---

## 2. Microservice Runtimes & Compute Isolation

To prevent compute starvation and isolate heavy processing workloads from synchronous requests, the raster and vector pipelines cleanly segregate their GDAL runtimes:

### 2.1 The Shared `gdal-microservice`
- **Type**: Synchronous FastAPI / Python Cloud Run service (`https://gdal-microservice-534590904912.us-central1.run.app`).
- **Role**: Read-only lightweight file analysis. The `ugs-ingest` frontend and Express backend proxy calls here for fast, synchronous `gdalinfo -json -stats` and `ogrinfo` queries to inspect files during initial upload/analysis.
- **Constraints**: Sized with a strict 30MB multipart upload proxy limit (staged via GCS for larger files) and a 500MB hard limit. It NEVER performs heavy conversions or projections for the raster pipeline to ensure maximum availability.

### 2.2 In-Process Processor Nodes
- **Type**: Asynchronous specialized processor nodes on Cloud Run / Cloud Run Jobs.
- **Role**: Heavy geospatial transformations (`gdalwarp`, `-of COG` conversion, custom `gdal_footprint` extraction, `tippecanoe` tiling).
- **Instances**:
  - `raster-ingest-processor`: Dedicated Cloud Run service running in `ugs-ingest` to handle Track A file finalization events asynchronously (sized at $\ge$4Gi CPU/RAM with VSI streaming support).
  - `geolmap-harvest`: Sized at 4Gi RAM, 2 vCPU to process large series zip packages, extracting vectors, compiling `units.pmtiles` and creating publication-level footprints.

---

## 3. Storage & Format Standards

We support three cloud-native storage tiers based on data dimensionality and analytical use cases:

### A. Cloud-Optimized GeoTIFF (COG)
*Primary format for 2D spatial layers (e.g., orthoimagery, single-date elevation, hazard susceptibility).*
- **Internal Structure**: Tiled pixels (`256x256` or `512x512`), internally sorted with downsampled overviews (pyramids).
- **Access Pattern**: Allows GIS clients (QGIS, ArcGIS, MapLibre via TiTiler) to stream specific spatial sub-regions and zoom levels using HTTP Range Requests, bypassing the need to download the full file.
- **Compression**: WebP (for RGB imagery/visual overlays) or LZW/Deflate with horizontal differencing predictor (for continuous scientific values/dem).

### B. Zarr / Icechunk (Multi-Dimensional Space-Time Datacubes)
*Primary format for time-series raster models (e.g., the soil-water model, daily temperature grids).*
- **Zarr**: Stores multi-dimensional gridded arrays (e.g., `(time, elevation, lat, lon)`) as small, compressed N-dimensional chunks in GCS. Analytical libraries (`xarray`, `Dask`, `pandas`) can open Zarr datasets directly from GCS and fetch chunks in parallel.
- **Icechunk**: A transactional storage engine built specifically for Zarr. It enables Git-like versioning on top of GCS-backed Zarr arrays:
  - **Branches / Commits**: Create a draft branch, run model iterations, commit, and safely merge new date slices into the main production datacube.
  - **No-copy Rewrites**: Keeps historical versions accessible via transaction IDs without duplicating files in GCS.
- **Access Pattern**: Highly optimized for temporal slicing (e.g., "Give me the last 10 years of values at coordinate X,Y") or spatial slicing across time.

---

## 4. Zero-Infrastructure Download & Conversion (WebAssembly)

To let users download data in their preferred legacy formats (e.g., Esri Grid, GeoTIFF, NetCDF, PNG, or CSV) without taxing our GCP compute budget, we employ a **Client-Side WebAssembly (Wasm) Conversion** strategy.

### The Flow:
1. **User Selection**: In the browser, the user selects a dataset, time range, bounding box, and target format.
2. **Sub-region Range Read**: The client-side application uses `geotiff.js` or `h5web` to fetch **only** the selected spatial/temporal chunk from the GCS-backed COG or Zarr store via HTTP Range Requests (direct from CDN).
3. **In-Browser Translation (Wasm)**:
   - The browser loads a lightweight WebAssembly build of **GDAL** (e.g., `@gdal/gdal` or custom `gdal3.wasm` build).
   - The fetched pixel chunks are passed to the GDAL Wasm virtual file system.
   - GDAL translates the chunks to the user's preferred format (e.g., `gdal_translate -of AAIGRID /vsimem/input.tif /vsimem/output.asc`).
4. **Local Save**: The generated file is saved directly to the user's local disk via browser-native save streams.

### Zero-Infra Architectural Advantages:
- **Zero Server Cost**: Conversion compute is pushed to the client’s machine. No spinning up heavy Celery workers or running expensive GDAL-loaded servers.
- **Infinite Scalability**: 10,000 concurrent users performing conversions scale instantly at no extra cost to UGS.
- **No Storage Bloat**: No need to pre-generate and store 10 different file formats in GCS. Only the master COG/Zarr datacube is saved.

---

## 5. Per-Layer Edition STAC Schema

Per the ugs-ingest #169 contract (2026-07-23), **every** raster layer is a versioned
**Collection of dated Items** — the standard cloud-native-geospatial time-series shape, applied
uniformly (no separate shared "1-off" collection). Each edition is an append-only row in
`raw.raster_catalog` with `is_current` marking the live one; nothing overwrites. "Latest" falls
out of `datetime` ordering; the STAC Versioning extension is added only when an edition is
superseded/corrected (same date republished).

```
ugs-warehouse-stac/
├── catalog.json                                # Main Warehouse Catalog
└── ugs-raster-soil-water/                      # Collection for the Soil-Water layer
    ├── collection.json                         # Declares extent (+ Zarr datacube link for time-series)
    ├── soil_water_20260601T000000/             # Edition: June 1st  (is_current=false once superseded)
    │   └── soil_water_20260601T000000.json     # Points to that edition's COG
    └── soil_water_20260602T000000/             # Edition: June 2nd  (is_current=true)
        └── soil_water_20260602T000000.json
```

- `item_id` and `collection` are ingest-authored (`{piece}_{pubid}_{pubdate}`, piece==layer for
  single-COG topics); the warehouse owns only the COG path convention (`cog/<layer>/<item_id>.*`).
- `datetime` = `publication_date` and is **never null**.
- Collections are **nested** under a `ugs-rasters/` sub-catalog: ingest emits
  `collection = "ugs-rasters/<layer>"` (the layout path); the STAC collection id is the last
  segment (`<layer>`), mirroring `ugs-publications/<series>`. Keeps the catalog root clean and
  lets the viewer's newest-first list include them as dated publications.

### Double-Integration:
- **File Downloaders (STAC Item Seekers)**: Can search the collection by datetime, discover the specific dated STAC item, and retrieve that edition's COG asset.
- **Datacube Analysts (Python / Xarray Seekers)**: Can open the Zarr datacube asset listed in the root `collection.json` to stream multi-dimensional slices over space/time in parallel.

---

## 6. Storage Directory Layout (GCS)

All data files land under dedicated prefixes to maintain isolation and separation of concerns:

```
gs://ut-dnr-ugs-maps-prod-public/
├── warehouse/
│   └── stac/                                   # Unified discovery catalog
│       ├── catalog.json
│       ├── ugs-serving-topics/
│       └── ugs-raster-soil-water/              # Time-series collection
│           ├── collection.json
│           └── soil_water_{datetime}/...
│
├── cog/                                       # COG editions, per artifact-type sibling of pmtiles/ etc.
│   └── <layer>/                               # one dir per layer (e.g. slope/, soil_water/)
│       ├── <item_id>.cog.tif                  # e.g. slope_OFR123_20260601.cog.tif
│       └── <item_id>.thumb.png
│
└── datacubes/                                 # Multi-dimensional arrays (time-series layers)
    └── soil_water.zarr/                       # Zarr store / Icechunk chunks
        ├── .zgroup
        ├── .zmetadata
        ├── time/
        ├── lat/
        └── value/
```

---

## 7. Ingestion Pipeline Orchestration (`raster/ingest.py`)

Each run processes an incoming raw gridded raster file (GeoTIFF / NetCDF) and coordinates the execution sequence:

```
Raw File / Stream ─► source.read() ─► Temp Workspace
                                         │
 ┌───────────────────────────────────────┴────────────────────────────────────────┐
 ▼ (Transform & Warp)                                                             ▼ (Datacube Sink)
gdalwarp -t_srs EPSG:3857 -r bilinear -co TILED=YES                              If time-series:
rio-cogeo create -b 1 --co COMPRESS=deflate                                      Write/Append chunk
gdal_translate -of PNG -outsize 500 0 (Thumbnail)                                to GCS Zarr/Icechunk
 │                                                                                │
 ▼ (COG Sink)                                                                     │
Upload COG + Thumbnail to GCS                                                    │
 │                                                                                │
 └───────────────────────────────────────┬────────────────────────────────────────┘
                                         ▼
                                  sink_stac.build_item()
                                         │
                                         ▼
                                  write_item() ─► warehouse/stac/...
                                         │
                                         ▼
                                  stac.refresh_catalog()
```

### Orchestrator Arguments:
- `--layer <layer_name>`: Unique layer name (e.g., `soil_water`).
- `--source <path_or_url>`: Path/URL to input GeoTIFF/NetCDF.
- `--datetime <iso_string>`: ISO 8601 string for time-series slicing. If omitted, processed as a 1-off snapshot.
- `--dry-run`: Validate coordinate bounds and metadata without committing uploads.
- `--force`: Overwrite existing artifacts of identical ID.
