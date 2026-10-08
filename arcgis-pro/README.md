# UGS Warehouse toolbox for ArcGIS Pro

Adds warehouse layers to the active map: vector layers styled with the web viewer's colors and
legend, and geologic map rasters.

## Install

1. Put `UGSWarehouse.pyt` and `ugs_catalog.py` in the same folder.
2. In Pro: **Catalog pane → Toolboxes → right-click → Add Toolbox**, and pick `UGSWarehouse.pyt`.

Nothing else to install; the toolbox uses only Pro's own Python (including its `pyarrow`).

## Use

Open **UGS Warehouse → Add Warehouse Layer**:

- **Theme** narrows the list (`emp`, `hazards`, `mapping`, `wetlands`, and
  `geologic maps (raster)`: the 870 map scans and rasters with a COG on the CDN).
- **Search** narrows it further: every word must appear in the layer's title, id or keywords.
- **Layers**: pick one or more.
- **Source**:
  - **Open online** (Pro 3.5 or later): the layer reads the warehouse bucket through a cloud
    storage connection the tool creates in the working folder. Map images (COGs) open online with
    a Google sign-in; GeoParquet needs a service account key (see "How streaming works") and is
    otherwise downloaded.
  - **Copy to a file geodatabase**: any Pro version. Downloads the GeoParquet (checked against the
    catalog's size and checksum) and writes a feature class to `UGS Warehouse.gdb` in the working
    folder, for offline work or editing.
- **Style like the web viewer**: categories, colors and legend labels from the layer's style. A
  layer with no style, or a style with no simple equivalent, keeps Pro's default symbol.

A raster (a COG) opens through the bucket with your sign-in, then through Esri's generic HTTP
connection to the CDN; Pro reads only the part in view. A copy writes each layer in the CRS its
GeoParquet metadata names (WGS84 when it names none; a CRS with no EPSG code gets an unknown one
and a warning), a Parquet with no geometry as a table, and a GeoJSON through Esri's JSONToFeatures,
one geometry type per layer. Every layer gets the
catalog's title, summary, description, tags, credits and license in its metadata (a geodatabase
copy's feature class gets it when Pro keeps the layer's own metadata read-only).

If opening online does not work on a machine, or Pro is older than 3.5, the tool copies the layer
instead and says so in its messages, so a run always ends with the layer on the map.

## Sign in (to open map images online)

The data sits in a private Google Cloud Storage bucket. Opening a map image online reads the
bucket with your own Google sign-in:

1. Install the [Google Cloud CLI](https://cloud.google.com/sdk/docs/install).
2. Run **UGS Warehouse → Sign In to UGS Storage** and sign in with your utah.gov account.

Your account needs read access to the bucket (ask the warehouse admins). Without a sign-in, or
without access, a map image falls back to the CDN, and if nothing opens it the tool names each
reason. GeoParquet is downloaded either way, as "How streaming works" explains.

## Update

Run **UGS Warehouse → Update Toolbox**. It downloads both files from GitHub, replaces them only if
both arrived whole (keeping `.bak` copies), and says what changed. **Branch** is `main` for
released versions; enter a pull request's branch to test it, and the toolbox remembers it.
Afterwards, right-click the toolbox and choose **Refresh**: Pro reads a `.pyt` once.

When GitHub has a newer version, **Add Warehouse Layer** says so at the top of its dialog. It
checks once per Pro session and stays quiet when GitHub cannot be reached.

## How streaming works

ArcGIS Pro opens Parquet only through a cloud storage connection (Amazon S3, Azure, Google), not
from a web address, and Esri's generic HTTP provider (`WEB`) opens rasters only. So every layer
opened online goes through a Google connection to the bucket itself; a raster can also use a
`WEB` connection to the CDN.

Pro's Parquet reader accepts fewer Google options than its raster reader: anonymous access
(`GS_NO_SIGN_REQUEST`) or a service account key (`GOOGLE_APPLICATION_CREDENTIALS`). A personal
`gcloud` sign-in opens rasters but not GeoParquet, so the toolbox downloads GeoParquet for it and
says why. The sign-in file's path goes to Pro with forward slashes: Pro reads backslashes in that
option as escapes.

An S3 connection with the CDN as its endpoint does not work: Pro sends its requests to Amazon
instead of the endpoint (or, as MinIO, puts the bucket name in the host name). An `s3://` or
`gs://` href opens anonymously on that provider's own endpoint.

## What it reads from the catalog

A catalog change to any of these can break the toolbox; check here first.

| Where | What |
|---|---|
| `warehouse/stac/ugs-serving-topics/items.json` | `items[].id`; `properties.title`, `ugs:dbt_schema`, `keywords` |
| `warehouse/stac/items.json` (root) | `items[].id`; `assets.cog.href` on the CDN; `properties.title`, `ugs:series_id`, `ugs:author`, `ugs:scale`, `keywords` |
| Item `ugs-serving-topics/<schema>/<id>/<id>.json` | `assets.data.href`, `file:size`, `file:checksum`; `properties.title`, `description`, `keywords`, `ugs:point_of_contact`, `ugs:renders.default.style_url` and `.legend` |
| `ugs-serving-topics/<schema>/collection.json` | `license`, the `rel: license` link, `providers[].name` |
| The GeoParquet | the `geo` metadata's `primary_column` and `geometry_types`; WKB geometry |
| The MapLibre style | `fill`/`line`/`circle` layers: `filter`, and `fill-color`/`line-color`/`circle-color` |
| Layout | data under `https://maps-assets.geology.utah.gov/warehouse/`, COGs under `/geolmap/` |

## Code

`ugs_catalog.py` reads the STAC catalog and the layer's MapLibre style and converts types;
`UGSWarehouse.pyt` does the map work. Both are tested off Windows in `tests/test_arcgis_pro.py`,
against a fake arcpy.
