# UGS Warehouse — User Guide

How to get UGS geologic data into whatever tool you use. No account, no login — the data is
public on a CDN. Pick your tool below and copy-paste.

> Running example throughout: the **`hazards_qfaults`** layer (Quaternary faults). Swap that id
> for any layer you find in the [catalog](#finding-data).

---

## What's published (30-second primer)

Every layer is offered as several **cloud-native** formats. You don't convert anything — you point
your tool at a URL.

| Format | What it is | Best for | URL pattern |
|---|---|---|---|
| **GeoParquet** | the full vector dataset, one file, queryable + range-readable | Python / R / DuckDB / GDAL / BI | `…/warehouse/geoparquet/{id}/{id}.parquet` |
| **PMTiles** | pre-built vector map tiles, one file | web maps, QGIS | `…/warehouse/pmtiles/{id}/{id}.pmtiles` |
| **COG** | Cloud-Optimized GeoTIFF (scanned geologic maps) | QGIS / Pro / rasterio | `…/geolmap/cogs/{series}.cog.tif` |
| **STAC** | the catalog/index of everything (JSON) | discovery, scripting | `…/warehouse/stac/catalog.json` |
| **OGC API Features** | a live, server-side-queryable feature *service* | ArcGIS Pro / QGIS / AGOL | `{OGC_API_BASE}/collections/{id}/items` |

CDN base for all of the above: **`https://maps-assets.geology.utah.gov`**

Two things that are true everywhere:
- **CRS is always EPSG:4326** (WGS84 lon/lat). No reprojection guesswork.
- GeoParquet files carry **`bbox_xmin/ymin/xmax/ymax`** covering columns, so tools that support it
  only read the rows in your area (spatial pushdown) instead of the whole file.

> **OGC API base:** the `{OGC_API_BASE}` placeholder is the `ugs-warehouse-features` service URL —
> fill it in from the viewer's "OGC API" link (or ask the data team). A collection id == the layer id.

---

## Pick your tool

- [Web viewer (no install)](#web-viewer) — just browse/download in a browser
- [ArcGIS Pro](#arcgis-pro)
- [QGIS](#qgis)
- [Python](#python)
- [R](#r)
- [DuckDB](#duckdb)
- [GDAL / ogr2ogr (command line)](#gdal--ogr2ogr)
- [BI tools (Tableau / Power BI)](#bi-tools)
- [Finding data](#finding-data)

---

## Web viewer
*No install. The fastest way to see what exists and grab a slice.*

Open **<https://maps-assets.geology.utah.gov/warehouse/viewer/>**

- Browse the catalog, search, filter by attribute.
- Click a layer → interactive map + a full data table (sortable/filterable).
- Click a feature on the map or a row in the table — they stay in sync.
- **Export** any layer (whole or clipped to your view) to SHP / GeoPackage / FileGDB /
  FlatGeobuf / GeoJSON / CSV — all in the browser.

Good for: a quick look, a one-off download, sharing a link. For repeatable/large work use one of the
tools below.

---

## ArcGIS Pro
*Most UGS staff. Pro reads several of these natively (Pro 3.2+ recommended).*

**OGC API Features (best for editable/queryable vector layers):**
1. **Insert → Connections → Server → New OGC API Server** (or *Catalog pane → Servers → right-click → New OGC API Server*).
2. Server URL: `{OGC_API_BASE}`
3. Expand the connection → drag the collection (e.g. `hazards_qfaults`) onto the map. It's a live,
   server-side-queryable layer (real spatial query, not a download).

**Geologic-map rasters (COG) — no service needed:**
- **Add Data → From Path** and paste the COG URL, e.g.
  `https://maps-assets.geology.utah.gov/geolmap/cogs/M-299DM.cog.tif`
- Pro range-reads the COG over HTTP — only the pixels in view download.

**Discovery (STAC):** Pro 3.x can add a **STAC connection** to
`https://maps-assets.geology.utah.gov/warehouse/stac/catalog.json` to browse the catalog and add
COG items directly.

> GeoParquet: Pro's direct Parquet support is limited/version-dependent — prefer the **OGC API**
> connection for vector, or download via the [web viewer](#web-viewer) / [ogr2ogr](#gdal--ogr2ogr) to
> a File GDB.

---

## QGIS
*A few UGS staff + the wider community. QGIS 3.28+ for OGC API, 3.32+ for PMTiles.*

**OGC API Features:** Browser panel → right-click **WFS / OGC API – Features → New Connection** →
URL `{OGC_API_BASE}` → expand → add the `hazards_qfaults` layer.

**GeoParquet (direct, via GDAL):** Layer → Add Layer → Add Vector Layer → Source type *Protocol HTTP(S)*,
or just open the path:
```
/vsicurl/https://maps-assets.geology.utah.gov/warehouse/geoparquet/hazards_qfaults/hazards_qfaults.parquet
```
(Requires a QGIS build with the GDAL Parquet driver — most 3.34+ installs.)

**PMTiles (fast basemap-style vector tiles):** Layer → Add Layer → **Add Vector Tile Layer → New
Generic Connection**, URL:
```
pmtiles://https://maps-assets.geology.utah.gov/warehouse/pmtiles/hazards_qfaults/hazards_qfaults.pmtiles
```

**COG (raster maps):** Layer → Add Raster Layer → *Protocol HTTP(S)*, or:
```
/vsicurl/https://maps-assets.geology.utah.gov/geolmap/cogs/M-299DM.cog.tif
```

---

## Python
*Analysts / data science. Two solid paths: DuckDB (no full download) or GeoPandas.*

**DuckDB — query without downloading the whole file (recommended):**
```python
import duckdb
con = duckdb.connect()
con.execute("INSTALL httpfs; LOAD httpfs; INSTALL spatial; LOAD spatial;")

URL = "https://maps-assets.geology.utah.gov/warehouse/geoparquet/hazards_qfaults/hazards_qfaults.parquet"

# attributes only — streams, fast
df = con.execute(f"SELECT * EXCLUDE (geom) FROM read_parquet('{URL}') LIMIT 1000").df()

# spatial: only features in a bbox (uses the bbox covering columns → reads a fraction of the file)
rows = con.execute(f"""
    SELECT *, ST_AsText(geom) AS wkt
    FROM read_parquet('{URL}')
    WHERE bbox_xmin < -111.8 AND bbox_xmax > -112.0
      AND bbox_ymin <  40.8 AND bbox_ymax <  41.0
""").df()
```

**GeoPandas — get a GeoDataFrame:**
```python
import geopandas as gpd
gdf = gpd.read_parquet(
    "https://maps-assets.geology.utah.gov/warehouse/geoparquet/hazards_qfaults/hazards_qfaults.parquet"
)  # needs `requests`/`aiohttp` for the http read; or download the file first
gdf.plot()
```

**Discovery (STAC) with pystac:**
```python
import pystac
cat = pystac.Catalog.from_file("https://maps-assets.geology.utah.gov/warehouse/stac/catalog.json")
for col in cat.get_children():
    print(col.id)
```

---

## R
*sf + arrow/DuckDB. Same data, R-native.*

**sf via GDAL (one call):**
```r
library(sf)
url <- "/vsicurl/https://maps-assets.geology.utah.gov/warehouse/geoparquet/hazards_qfaults/hazards_qfaults.parquet"
faults <- st_read(url)          # needs a GDAL with the Parquet driver
plot(st_geometry(faults))
```

**DuckDB (query without a full download):**
```r
library(duckdb); library(DBI)
con <- dbConnect(duckdb())
dbExecute(con, "INSTALL httpfs; LOAD httpfs; INSTALL spatial; LOAD spatial;")
url <- "https://maps-assets.geology.utah.gov/warehouse/geoparquet/hazards_qfaults/hazards_qfaults.parquet"
df  <- dbGetQuery(con, sprintf(
  "SELECT *, ST_AsText(geom) AS wkt FROM read_parquet('%s') LIMIT 1000", url))
```

**Discovery (STAC) with rstac:**
```r
library(rstac)
stac("https://maps-assets.geology.utah.gov/warehouse/stac") |> collections() |> get_request()
```

---

## DuckDB
*The lake's native query engine. CLI or any DuckDB binding.*

```sql
INSTALL httpfs; LOAD httpfs;
INSTALL spatial; LOAD spatial;

-- peek
SELECT * EXCLUDE (geom)
FROM read_parquet('https://maps-assets.geology.utah.gov/warehouse/geoparquet/hazards_qfaults/hazards_qfaults.parquet')
LIMIT 10;

-- spatial query with pushdown (only reads rows whose bbox overlaps the AOI)
SELECT count(*)
FROM read_parquet('https://maps-assets.geology.utah.gov/warehouse/geoparquet/hazards_qfaults/hazards_qfaults.parquet')
WHERE bbox_xmin < -111.8 AND bbox_xmax > -112.0
  AND bbox_ymin <  40.8 AND bbox_ymax <  41.0;

-- export a clip to GeoJSON
COPY (
  SELECT * EXCLUDE (geom), ST_AsText(geom) AS wkt
  FROM read_parquet('https://maps-assets.geology.utah.gov/warehouse/geoparquet/hazards_qfaults/hazards_qfaults.parquet')
) TO 'faults.csv' (HEADER);
```
Each layer also has dated, immutable snapshots next to the `latest` file
(`…/{id}/{id}_YYYYMMDD.parquet`) — cite those for reproducibility.

---

## GDAL / ogr2ogr
*Command line. Convert any layer to any format, no GUI.*

```bash
# GeoParquet → GeoPackage (downloads only what it needs via /vsicurl/)
ogr2ogr -f GPKG faults.gpkg \
  /vsicurl/https://maps-assets.geology.utah.gov/warehouse/geoparquet/hazards_qfaults/hazards_qfaults.parquet

# → Shapefile, clipped to a bbox (w s e n)
ogr2ogr -f "ESRI Shapefile" faults_clip.shp \
  /vsicurl/https://maps-assets.geology.utah.gov/warehouse/geoparquet/hazards_qfaults/hazards_qfaults.parquet \
  -spat -112.0 40.8 -111.8 41.0

# inspect a COG
gdalinfo /vsicurl/https://maps-assets.geology.utah.gov/geolmap/cogs/M-299DM.cog.tif
```

---

## BI tools
*Tableau / Power BI / Excel — non-GIS analysts.*

These read the **attributes** of a layer well; geometry handling is limited, so for maps prefer the
GIS tools above.

- **Power BI:** *Get Data → Parquet* (or *Web*) and point at a GeoParquet URL; or use the
  **CSV export** from the [web viewer](#web-viewer) for the simplest path. The lat/lon or `bbox_*`
  columns drive the Map / ArcGIS-for-Power-BI visual.
- **Tableau:** easiest is the **CSV export** from the viewer, or a Parquet connector. Plot points by
  lat/lon; for line/polygon layers, export to a spatial format (GeoPackage) via
  [ogr2ogr](#gdal--ogr2ogr) and use Tableau's spatial file connector.
- Tip: use **DuckDB** to pre-shape a Parquet/CSV (filter, pick columns, drop geometry) before loading.

---

## Finding data

- **Browse:** the [web viewer](#web-viewer), or open the STAC root in any STAC tool:
  `https://maps-assets.geology.utah.gov/warehouse/stac/catalog.json`
- **List every layer (DuckDB over the catalog index):**
  ```sql
  SELECT id FROM read_json_auto(
    'https://maps-assets.geology.utah.gov/warehouse/stac/ugs-serving-topics/items.json'
  ) t, UNNEST(t.items) AS u(item)  -- ids are under .items[].id
  ```
- A layer id (e.g. `hazards_qfaults`, `enmin_oilgasfields_ogm`) plugs into every URL pattern above,
  and is also the **OGC API collection id** and the STAC item id.

Each layer's STAC item describes its **fields** (`table:columns`), **categories + colors**
(`classification:classes`), CRS, extent, and links to every format — so you can script against the
catalog instead of hard-coding URLs.

---

*Questions or a layer that won't load? Contact the UGS data team.*
