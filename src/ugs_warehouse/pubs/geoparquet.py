"""GeoParquet writes for the pubs producers, through geoparquet-io (gpio)."""
from __future__ import annotations

ROW_GROUP_MB = 32  # matches the viewer's range reads; gpio's default is ~300 MB


def write(src, path) -> None:
    """Write a GeoDataFrame, or a vector file gpio can convert, as GeoParquet 1.1 with a bbox
    covering column, Hilbert-sorted, in ~32 MB row groups with a page index. A file's geometry
    column is `geom`."""
    import geoparquet_io as gpio

    if isinstance(src, str):
        conv = gpio.convert(src, repair_geometry=False)  # keep source geometry as published
        names = ["geom" if c == conv.geometry_column else c for c in conv.column_names]
        table = gpio.Table(conv.table.rename_columns(names), geometry_column="geom", crs=conv.crs)
    else:
        import pyarrow as pa

        crs = src.crs.to_json_dict() if src.crs else None
        arrow = pa.table(src.to_arrow(index=False, geometry_encoding="WKB"))
        table = gpio.Table(arrow, geometry_column=src.geometry.name, crs=crs)
    table.add_bbox().sort_hilbert().write(
        str(path), geoparquet_version="1.1", row_group_size_mb=ROW_GROUP_MB)
    _page(path)


def _page(path) -> None:
    """Rewrite `path` with a page index and the serving-topic archive's page sizes, so a reader
    fetches only the pages a bbox touches. gpio's writer has no page options; this streams one
    row group at a time and keeps the schema, geo metadata and row groups as they are."""
    import os

    import pyarrow as pa
    import pyarrow.parquet as pq

    from ..vector.sink_archive import PAGE_BYTES, PAGE_ROWS

    tmp = f"{path}.paged"
    try:
        with pq.ParquetFile(path) as src:
            schema = src.schema_arrow
            dictionary = [f.name for f in schema if pa.types.is_string(f.type) or
                          pa.types.is_large_string(f.type) or pa.types.is_integer(f.type)]
            with pq.ParquetWriter(tmp, schema, compression="zstd", compression_level=9,
                                  write_page_index=True, data_page_size=PAGE_BYTES,
                                  max_rows_per_page=PAGE_ROWS, write_batch_size=64,
                                  use_dictionary=dictionary) as out:
                for g in range(src.num_row_groups):
                    out.write_table(src.read_row_group(g))
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):  # a failed rewrite; /tmp is RAM on Cloud Run
            os.remove(tmp)
