# UGS Warehouse toolbox for ArcGIS Pro

Adds warehouse vector layers to the active map, styled with the web viewer's colors and legend.

## Install

1. Put `UGSWarehouse.pyt` and `ugs_catalog.py` in the same folder.
2. In Pro: **Catalog pane → Toolboxes → right-click → Add Toolbox**, and pick `UGSWarehouse.pyt`.

Nothing else to install; the toolbox uses only Pro's own Python (including its `pyarrow`).

## Use

Open **UGS Warehouse → Add Warehouse Layer**:

- **Theme** narrows the list (`emp`, `hazards`, `mapping`, `wetlands`).
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

If streaming does not work on a machine, or Pro is older than 3.5, the tool copies the layer
instead and says so in its messages, so a run always ends with the layer on the map.

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
endpoints, so the messages name the connection that was used.

Pro rejects nested Parquet columns, and the warehouse GeoParquet carries the GeoParquet 1.1
`bbox` struct. A copy drops it (the flat `bbox_*` columns keep the extent) and renames columns to
Esri's rules; a stream reads the file as published.

## Code

`ugs_catalog.py` reads the STAC catalog and the layer's MapLibre style and converts types;
`UGSWarehouse.pyt` does the map work. Both are tested off Windows in `tests/test_arcgis_pro.py`,
against a fake arcpy.
