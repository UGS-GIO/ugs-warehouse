# Raster path

**Status: mostly design, with a provisional scaffold.** The raster→STAC mapping is **built and
tested** (`raster/consume.py` `stac_item_from_record`, `raster/sink_stac.py`, `tests/test_raster.py`)
— a raster record becomes a STAC item in the shared catalog. What's **blocked / not wired**: the COG
**promote** step (`promote()` raises `NotImplementedError` — the staged COG lives in a separate bucket
and needs a cross-bucket copy) and the `raw.raster_catalog` schema, both pending **ugs-ingest #169**;
the module isn't called from the live ingest/service yet. Driver: the soil-water-model project (raster
time-series) plus one-off raster layers. This doc captures the target design.

## Principle

A raster layer-at-a-point-in-time is, at the **catalog** level, the same as a vector
one: a STAC item. So the discovery surface (STAC) and the storage substrate (GCS via
obstore) and the orchestration shape (`source → transform → sinks`) are **shared**.

What differs is **storage + serving** — rasters are gridded pixel arrays, not features.
So this is a **parallel pipeline** (`raster_ingest`), not an extension of `ingest.py`.
Don't overload the vector transform/sinks.

## Surface mapping (mirror the vector stack)

| Vector (built) | Raster (analog) | Role |
|---|---|---|
| GeoParquet archive | **COG** (Cloud-Optimized GeoTIFF) | open, vendor-neutral download/interop — the primary artifact |
| DuckLake table | **RaQuet** (raster-in-parquet) | raster queryable in the lakehouse alongside vectors — optional |
| PMTiles | raster tiles / titiler | web-map serving |
| STAC item + catalog | **STAC item + catalog** | discovery — shared, format-agnostic |

## Recommendation

1. **Primary: COG + STAC.** COG is the raster analog of GeoParquet — the cloud-native
   standard, read by GDAL/QGIS/titiler/every web map, zero lock-in. For "expose a raster
   layer," this is the whole answer. Build first.
2. **RaQuet: optional, experimental.** Lets rasters be SQL-queried in DuckDB alongside
   vectors (raster↔vector joins) — the unified-warehouse upside. But it's CARTO-origin,
   lower adoption, and DuckDB support is a **community extension** ([raquet ext](https://duckdb.org/community_extensions/extensions/raquet))
   — the same security-review + maintenance-rot risk that ruled out the `gcs` community
   ext. So: analytical convenience layer, **not** the interop/serving surface partners
   depend on. Gate on security review.
3. **Don't put rasters through httpfs for GCS** — same HMAC block as vectors. Reuse the
   obstore / obstore-fsspec path (see `AGENTS.md` "GCS IO").

## Time dimension

This is the real fork (a single raster is the easy case, not the common one):

- **One-off snapshot** → single COG + one STAC item. Simplest; fits the current model.
- **Time-series** (the soil-water model) → either
  - a STAC **collection** of COGs, one item per timestamp ("give me date X"), or
  - a **Zarr datacube** (xarray) if consumers need to *slice an array across time/space*.
  Zarr is the established datacube standard; prefer it when the access pattern is
  multi-dimensional array math rather than per-date file download.

Decide per consumer need: file-download-per-date → COG collection; array analytics → Zarr.

## Shared vs new

**Reuse:** STAC item + the auto-refreshed root catalog (`sink_stac`), GCS substrate,
obstore IO, the `source → transform → sinks` pattern, the per-sink isolation + geometry
(here: validity) guard idea.

**New (`raster_ingest`):**
- source: GeoTIFF / NetCDF / Zarr / arrays (not PostGIS `geom`)
- transform: warp/reproject to 4326 (or the target grid), retile, build overviews
- sinks: `sink_cog`, optional `sink_raquet`, optional raster-tiles, and the STAC item
  (reusing the vector STAC catalog so vector + raster share one discovery surface)

## Open questions

- Source of truth for raster inputs — where does the soil-water model publish (GCS COGs
  already? NetCDF? a compute job output)? Determines the `source` shape.
- One catalog for both vector + raster, or separate collections under one root? (Lean:
  one root catalog, a raster collection alongside vector items.)
- Zarr vs COG-collection for the soil-water time-series — pending the consumer access
  pattern.
- titiler / dynamic raster serving — needed, or are static COGs + a viewer enough?
