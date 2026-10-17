"""GeoParquet writes for the pubs producers, through geoparquet-io (gpio)."""
from __future__ import annotations

import sys

ROW_GROUP_MB = 32  # matches the viewer's range reads; gpio's default is ~300 MB


def blank_non_finite(gdf) -> int:
    """Set to null, in place, each geometry with an infinite or NaN coordinate and return how many.
    They have no usable extent, which breaks the Hilbert sort; the row keeps its attributes like any
    row without a geometry."""
    import numpy as np

    geoms = gdf.geometry
    bad = ~np.isfinite(geoms.bounds.to_numpy()).all(axis=1) & ~(geoms.isna() | geoms.is_empty).to_numpy()
    if bad.any():
        gdf[gdf.geometry.name] = geoms.mask(bad, None)
    return int(bad.sum())


def write(src, path) -> None:
    """Write a GeoDataFrame, or a vector file gpio can convert, as GeoParquet 1.1 with a bbox
    covering column, Hilbert-sorted, in ~32 MB row groups. A file's geometry column is `geom`."""
    import geoparquet_io as gpio

    if isinstance(src, str):
        conv = gpio.convert(src, repair_geometry=False)  # keep source geometry as published
        names = ["geom" if c == conv.geometry_column else c for c in conv.column_names]
        table = gpio.Table(conv.table.rename_columns(names), geometry_column="geom", crs=conv.crs)
    else:
        import pyarrow as pa

        blanked = blank_non_finite(src)
        if blanked:
            print(f"  {path}: {blanked} geometries with a non-finite coordinate written as null",
                  file=sys.stderr)
        crs = src.crs.to_json_dict() if src.crs else None
        arrow = pa.table(src.to_arrow(index=False, geometry_encoding="WKB"))
        table = gpio.Table(arrow, geometry_column=src.geometry.name, crs=crs)
    table.add_bbox().sort_hilbert().write(
        str(path), geoparquet_version="1.1", row_group_size_mb=ROW_GROUP_MB)
