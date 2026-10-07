# UGS Warehouse toolbox for ArcGIS Pro

Adds warehouse vector layers to the active map, styled with the web viewer's colors and legend.

## Install

1. Put `UGSWarehouse.pyt` and `ugs_catalog.py` in the same folder.
2. In Pro: **Catalog pane → Toolboxes → right-click → Add Toolbox**, and pick `UGSWarehouse.pyt`.

Nothing else to install; the toolbox uses only Pro's own Python.

## Use

Open **UGS Warehouse → Add Warehouse Layer**:

- **Theme** narrows the list (`emp`, `hazards`, `mapping`, `wetlands`).
- **Layers**: pick one or more.
- **Source**:
  - **GeoParquet (download)**: the warehouse file, saved to the download folder and checked
    against the catalog's size and checksum. Needs Pro 3.5 or later. A file already downloaded and
    unchanged is reused.
  - **Live (OGC API Features)**: queried from the feature service; nothing is downloaded.
- **Style like the web viewer**: categories, colors and legend labels from the layer's style. A
  layer with no style, or a style with no simple equivalent, keeps Pro's default symbol.

## How it works

`ugs_catalog.py` reads the STAC catalog at `maps-assets.geology.utah.gov/warehouse/stac` and the
layer's MapLibre style; `UGSWarehouse.pyt` adds the layer and builds a unique-values renderer.
The catalog logic is tested off Windows in `tests/test_arcgis_pro.py`, against a fake arcpy.
