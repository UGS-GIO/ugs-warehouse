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
  - **Stream GeoParquet from the CDN** (Pro 3.5 or later): the layer reads the warehouse
    GeoParquet from `maps-assets.geology.utah.gov` through a cloud storage connection the tool
    creates in the working folder. Nothing is downloaded by hand.
  - **Copy to a file geodatabase**: any Pro version. Downloads the GeoParquet (checked against the
    catalog's size and checksum) and writes a feature class to `UGS Warehouse.gdb` in the working
    folder, for offline work or editing.
- **Style like the web viewer**: categories, colors and legend labels from the layer's style. A
  layer with no style, or a style with no simple equivalent, keeps Pro's default symbol.

A raster (a COG) opens through the same CDN connections as the GeoParquet, with `geolmap` as the
bucket, and its https URL as the last try; Pro reads only the part in view. Every layer gets the
catalog's title, summary, description, tags, credits and license in its metadata (a geodatabase
copy's feature class gets it when Pro keeps the layer's own metadata read-only).

If streaming does not work on a machine, or Pro is older than 3.5, the tool copies the layer
instead and says so in its messages, so a run always ends with the layer on the map.

## Sign in (to open layers online)

The data sits in a private Google Cloud Storage bucket. ArcGIS Pro opens GeoParquet only from
cloud storage (Amazon S3, Azure, Google), not from a web address, so opening a layer online reads
the bucket with your own Google sign-in:

1. Install the [Google Cloud CLI](https://cloud.google.com/sdk/docs/install).
2. Run **UGS Warehouse → Sign In to UGS Storage** and sign in with your utah.gov account.

Your account needs read access to the bucket (ask the warehouse admins). Without a sign-in, or
without access, Add Warehouse Layer downloads a copy instead and says why.

## Update

Run **UGS Warehouse → Update Toolbox**. It downloads both files from GitHub, replaces them only if
both arrived whole (keeping `.bak` copies), and says what changed. **Branch** is `main` for
released versions; enter a pull request's branch to test it, and the toolbox remembers it.
Afterwards, right-click the toolbox and choose **Refresh**: Pro reads a `.pyt` once.

When GitHub has a newer version, **Add Warehouse Layer** says so at the top of its dialog. It
checks once per Pro session and stays quiet when GitHub cannot be reached.

## How streaming works

The tool tries two cloud storage connections to the CDN, in order, and remembers the one that
works: Pro's generic HTTP provider (`WEB`), then an anonymous S3 connection with the CDN as its
endpoint. The second works because an unsigned path-style S3 read is a plain https GET, and the
CDN answers it with the range requests a Parquet reader needs. Esri does not certify S3-compatible
endpoints, so the messages name the connection that was used. A connection's bucket is the first
path segment of the file's URL (`warehouse` for GeoParquet, `geolmap` for the map COGs), so each
gets its own `.acs`.

Pro rejects nested Parquet columns, and the warehouse GeoParquet carries the GeoParquet 1.1
`bbox` struct. A copy drops it (the flat `bbox_*` columns keep the extent) and renames columns to
Esri's rules; a stream reads the file as published.

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
