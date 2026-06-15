"""Vector-topic STAC item — emitted through the shared core into the
`ugs-serving-topics` collection of the one catalog.

Assets: GeoParquet archive (`data`), PMTiles (`pmtiles`), DuckLake table (`ducklake`,
a gs:// locator read by DuckDB, not a browser). A web-map-links `pmtiles` link makes
STAC Browser v4+ render the actual layer. bbox/row_count come from the transformed view.
"""
from __future__ import annotations

import datetime

import duckdb

from ..core import config, gcs, iso, stac
from . import ducklake
from .topics import Topic

COLLECTION = "ugs-serving-topics"
PARQUET_MIME = "application/vnd.apache.parquet"
PMTILES_MIME = "application/vnd.pmtiles"


def _bbox(con: duckdb.DuckDBPyConnection, view: str) -> list[float]:
    row = con.execute(f"""
        SELECT MIN(ST_XMin(geom)), MIN(ST_YMin(geom)),
               MAX(ST_XMax(geom)), MAX(ST_YMax(geom))
        FROM {view}
    """).fetchone()
    return [float(row[0]), float(row[1]), float(row[2]), float(row[3])]


def _row_count(con: duckdb.DuckDBPyConnection, view: str) -> int:
    return int(con.execute(f"SELECT count(*) FROM {view}").fetchone()[0])


def write(topic: Topic, con: duckdb.DuckDBPyConnection, view: str,
          *, title: str | None = None, description: str | None = None) -> None:
    bbox = _bbox(con, view)
    now = datetime.datetime.now(datetime.UTC).isoformat()

    archive_path = f"{config.ARCHIVE_PREFIX}/{topic.stem}/{topic.stem}.parquet"
    pmtiles_path = f"{config.PMTILES_PREFIX}/{topic.stem}/{topic.stem}.pmtiles"
    pmtiles_url = config.public_url(pmtiles_path)
    # ducklake stays a gs:// locator — read by DuckDB, not a browser.
    ducklake_uri = f"{ducklake.DATA_PATH.rstrip('/')}/{topic.schema}/{topic.stem}"

    props = {
        "title": title or stac.prettify(topic.stem),
        "ugs:dbt_schema": topic.schema,
        "ugs:layer": topic.layer,
        "ugs:row_count": _row_count(con, view),
    }
    if description:
        props["description"] = description

    item = stac.build_item(
        item_id=topic.stem, collection=COLLECTION,
        geometry=stac.bbox_polygon(bbox), bbox=bbox, datetime_iso=now,
        properties=props,
        assets={
            "data": {"href": config.public_url(archive_path), "type": PARQUET_MIME,
                     "roles": ["data"], "title": "GeoParquet archive (native geometry)"},
            "pmtiles": {"href": pmtiles_url, "type": PMTILES_MIME,
                        "roles": ["visual"], "title": "PMTiles vector tiles"},
            "ducklake": {"href": ducklake_uri, "type": "application/x-ducklake-table",
                         "roles": ["data"], "title": "DuckLake table (native geometry)"},
        },
        extra_links=[stac.pmtiles_link(pmtiles_url, [topic.stem])],
        stac_extensions=[stac.WEB_MAP_LINKS_EXT],
        proj_epsg=4326,  # transform reprojects every topic to 4326
    )

    # ISO 19139 sidecar for gov clearinghouses (data.gov / state portals), linked as a
    # `metadata` asset. Generated from the item before this asset is added (no self-ref).
    iso_path = f"{config.STAC_PREFIX}/{COLLECTION}/{topic.stem}/{topic.stem}.iso.xml"
    gcs.put_bytes(iso.stac_to_iso19139(item).encode(), iso_path,
                  content_type="application/xml", cache_control=gcs.CACHE_MUTABLE)
    item["assets"]["metadata"] = {"href": config.public_url(iso_path), "type": "application/xml",
                                  "roles": ["metadata"], "title": "ISO 19139 metadata"}

    path = stac.write_item(item)
    print(f"[{topic.fqn}] stac: {config.public_url(path)}")
