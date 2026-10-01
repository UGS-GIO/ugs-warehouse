"""stac-geoparquet item mirror — one Parquet copy of a collection's items.

A client fetching a collection today makes one HTTP request per item. The mirror answers the same
question in one range request, and lets a consumer filter a collection spatially or temporally
without a STAC API. Portolan asks for it on raster collections (`PTL-MIR-001`); the item JSON stays
the normative copy and this is derived from it on every refresh, so the two cannot drift.

DuckDB writes it: it reads the item JSON, hydrates the geometry and emits GeoParquet with the
per-row-group statistics the format requires. No pyarrow — the base install stays free of it.
"""
from __future__ import annotations

import json
import os
import tempfile

from . import config, gcs
from .bbox import to_2d_bbox
from .geoparquet import geometry_types

OBJECT_NAME = "items.parquet"
ASSET_KEY = "items"
# Rows per row group. The spec caps it at 150,000 so a client can skip groups cheaply; our largest
# collection is far under that, so this is a ceiling rather than a tuning knob.
ROW_GROUP_SIZE = 150_000
GEOM = "ST_GeomFromGeoJSON(CAST(to_json(geometry) AS VARCHAR))"


def _connect():
    """A DuckDB connection with the spatial extension, loaded the way the vector path loads it."""
    import duckdb

    con = duckdb.connect()
    try:
        con.execute("LOAD spatial;")
    except duckdb.Error:
        con.execute("INSTALL spatial; LOAD spatial;")
    return con


def _extent(items: list[dict]) -> tuple[float, float, float, float]:
    """The union of the item bboxes. It contains every centroid, so it bounds the Hilbert key."""
    xmin, ymin, xmax, ymax = zip(*(to_2d_bbox(it["bbox"]) for it in items))
    return min(xmin), min(ymin), max(max(xmax), min(xmin) + 1e-9), max(max(ymax), min(ymin) + 1e-9)


def _geo_metadata(con, ndjson_path: str) -> str:
    """The GeoParquet 1.1 `geo` key with `bbox` declared as the covering. DuckDB writes 1.0 without it."""
    rows = con.execute(f"SELECT DISTINCT ST_GeometryType({GEOM})::VARCHAR, ST_HasZ({GEOM}) "
                       f"FROM read_json_auto('{ndjson_path}')").fetchall()
    types = geometry_types(rows)
    covering = {k: ["bbox", k] for k in ("xmin", "ymin", "xmax", "ymax")}
    return json.dumps({"version": "1.1.0", "primary_column": "geometry", "columns": {"geometry": {
        "encoding": "WKB", "geometry_types": types, "covering": {"bbox": covering}}}})


def _copy_sql(ndjson_path: str, out_path: str, extent: tuple[float, float, float, float],
              geo: str) -> str:
    """Item JSON -> GeoParquet.

    `geometry` arrives as parsed JSON, so it goes back through `to_json` to reach
    `ST_GeomFromGeoJSON`. `bbox` becomes the struct GeoParquet 1.1 expects. Rows are hilbert-ordered
    on the centroid over `extent`, the same ordering the archive sink uses, so row-group statistics
    prune well. Unbounded, ST_Hilbert orders by the raw float bits, which is not spatially local.
    """
    xmin, ymin, xmax, ymax = extent
    box = f"{{'min_x': {xmin!r}, 'min_y': {ymin!r}, 'max_x': {xmax!r}, 'max_y': {ymax!r}}}::BOX_2D"
    return f"""
        COPY (
          SELECT id, collection, type, stac_version, stac_extensions, properties, assets, links,
                 ST_AsWKB({GEOM}) AS geometry,
                 {{'xmin': bbox[1], 'ymin': bbox[2], 'xmax': bbox[len(bbox) // 2 + 1],
                   'ymax': bbox[len(bbox) // 2 + 2]}} AS bbox
          FROM read_json_auto('{ndjson_path}')
          ORDER BY ST_Hilbert(ST_Centroid({GEOM}), {box})
        ) TO '{out_path}'
        (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE {ROW_GROUP_SIZE},
         GEOPARQUET_VERSION 'NONE', KV_METADATA {{geo: '{geo}'}})
    """


def object_path(collection_path: str) -> str:
    return f"{config.STAC_PREFIX}/{collection_path}/{OBJECT_NAME}"


def write(collection_path: str, items: list[dict]) -> gcs.FileMeta | None:
    """Publish `items.parquet` beside a collection.json. Returns what the upload reported, or None.

    Best-effort by design: the mirror is derived data, and a refresh that cannot build it must
    still publish the collection. An item without geometry is skipped rather than written as a null
    row, since a mirror row that cannot be queried spatially is worse than an absent one.
    """
    spatial = [it for it in items if it.get("geometry") and it.get("bbox")]
    if not spatial:
        return None
    try:
        with tempfile.TemporaryDirectory() as tmp:
            ndjson = os.path.join(tmp, "items.ndjson")
            parquet = os.path.join(tmp, OBJECT_NAME)
            with open(ndjson, "w", encoding="utf-8") as fh:
                for it in spatial:
                    # stac_extensions is optional on an item, but the COPY selects it.
                    row = {"stac_extensions": [], **{k: v for k, v in it.items() if not k.startswith("_")}}
                    fh.write(json.dumps(row) + "\n")
            con = _connect()
            try:
                con.execute(_copy_sql(ndjson, parquet, _extent(spatial), _geo_metadata(con, ndjson)))
            finally:
                con.close()
            return gcs.upload(parquet, object_path(collection_path),
                              content_type=config.PARQUET_MIME,
                              cache_control=gcs.CACHE_MUTABLE)
    except Exception as e:  # noqa: BLE001 — derived data; never sink the refresh that publishes the collection
        print(f"[item-mirror] {collection_path}: SKIP ({e})")
        return None


def asset(collection_path: str, meta: gcs.FileMeta | None) -> dict:
    """The collection-level asset that registers the mirror.

    That registration is the whole requirement — the spec defines no `rel:"items"` link for it.
    """
    from . import stac

    if meta is None:
        return {}
    return {ASSET_KEY: {"href": config.public_url(object_path(collection_path)),
                        "type": config.PARQUET_MIME,
                        "roles": ["collection-mirror"],
                        "title": "Item mirror (stac-geoparquet)",
                        **stac.file_fields(meta)}}
