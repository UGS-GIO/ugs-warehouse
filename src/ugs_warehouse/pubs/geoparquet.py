"""GeoParquet writes for the pubs producers, through geoparquet-io (gpio)."""
from __future__ import annotations

ROW_GROUP_MB = 32  # matches the viewer's range reads; gpio's default is ~300 MB


def write(src, path) -> None:
    """Write a GeoDataFrame, or a vector file gpio can convert, as GeoParquet 1.1 with a bbox
    covering column, Hilbert-sorted, in ~32 MB row groups."""
    import geoparquet_io as gpio

    if isinstance(src, str):
        table = gpio.convert(src, repair_geometry=False)  # keep source geometry as published
    else:
        import pyarrow as pa

        crs = src.crs.to_json_dict() if src.crs else None
        arrow = pa.table(src.to_arrow(index=False, geometry_encoding="WKB"))
        table = gpio.Table(arrow, geometry_column=src.geometry.name, crs=crs)
    table.add_bbox().sort_hilbert().write(
        str(path), geoparquet_version="1.1", row_group_size_mb=ROW_GROUP_MB)
