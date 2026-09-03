# UGS Warehouse

The Utah Geological Survey **data warehouse** — a cloud-native lakehouse that publishes UGS geologic
data as open, standards-based formats anyone can consume: **GeoParquet, PMTiles, Cloud-Optimized
GeoTIFF, and a STAC catalog**, served from a CDN with (mostly) no running service.

## Start here

<div class="grid cards" markdown>

- :material-book-open-variant: **[User Guide](USER_GUIDE.md)**

    Get the data into your tool — ArcGIS Pro, QGIS, Python, R, DuckDB, GDAL, BI tools. Copy-paste,
    no account needed.

- :material-sitemap: **[Architecture](ARCHITECTURE.md)**

    How data flows from the source databases, through the warehouse, to the maps and services people
    use — with the current build status of each layer.

- :material-server-network: **[Serving tier](SERVING.md)**

    The access paths: static CDN artifacts vs the one scale-to-zero OGC API Features service.

- :material-palette: **[Styling](STYLING.md)**

    How `ugs-styles` binds cartography to catalog items via a `ugs:renders` block + `style` asset.

</div>

## What's published

| Format | What it is | For |
|---|---|---|
| **GeoParquet** | the full vector dataset, queryable + range-readable | Python / R / DuckDB / GDAL / BI |
| **PMTiles** | pre-built vector map tiles | web maps, QGIS |
| **COG** | Cloud-Optimized GeoTIFF (scanned geologic maps) | QGIS / Pro / rasterio |
| **STAC** | the catalog/index of everything | discovery, scripting |
| **OGC API Features** | a live, queryable feature service | ArcGIS Pro / QGIS / AGOL |

Everything is **EPSG:4326** and lives under **`https://maps-assets.geology.utah.gov`**.

The interactive **[STAC viewer](https://data-geology-utah-gov.web.app/)** is the
no-install way to browse, filter, and export.

---

*Maintained by the UGS data team. Source: the `ugs-warehouse` repository.*
