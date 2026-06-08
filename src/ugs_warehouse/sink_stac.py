"""Write a STAC Item per topic, referencing the warehouse artifacts.

Output:
  gs://{STAC_BUCKET}/{STAC_PREFIX}/{topic_stem}/{topic_stem}.json

Each item links the GeoParquet archive (`data`), the PMTiles (`pmtiles`), and
the DuckLake table location (`ducklake`). bbox + row_count are computed from
the DuckDB transformed view.

Env:
  WAREHOUSE_STAC_BUCKET  default ut-dnr-ugs-maps-prod-public
  WAREHOUSE_STAC_PREFIX  default warehouse/stac
"""
from __future__ import annotations

import datetime
import json
import os
import tempfile
from pathlib import Path

import duckdb
import obstore as obs
from obstore.store import GCSStore

from . import sink_archive, sink_pmtiles
from .catalog import DATA_PATH
from .topics import Topic

STAC_BUCKET = os.environ.get("WAREHOUSE_STAC_BUCKET", "ut-dnr-ugs-maps-prod-public")
STAC_PREFIX = os.environ.get("WAREHOUSE_STAC_PREFIX", "warehouse/stac")


def _bbox(con: duckdb.DuckDBPyConnection, view: str) -> list[float]:
    row = con.execute(f"""
        SELECT
          MIN(ST_XMin(geom)), MIN(ST_YMin(geom)),
          MAX(ST_XMax(geom)), MAX(ST_YMax(geom))
        FROM {view}
    """).fetchone()
    return [float(row[0]), float(row[1]), float(row[2]), float(row[3])]


def _row_count(con: duckdb.DuckDBPyConnection, view: str) -> int:
    return int(con.execute(f"SELECT count(*) FROM {view}").fetchone()[0])


def write(topic: Topic, con: duckdb.DuckDBPyConnection, view: str) -> None:
    bbox = _bbox(con, view)
    count = _row_count(con, view)
    now = datetime.datetime.now(datetime.UTC).isoformat()

    geometry = {
        "type": "Polygon",
        "coordinates": [[
            [bbox[0], bbox[1]],
            [bbox[2], bbox[1]],
            [bbox[2], bbox[3]],
            [bbox[0], bbox[3]],
            [bbox[0], bbox[1]],
        ]],
    }
    archive_uri = (
        f"gs://{sink_archive.ARCHIVE_BUCKET}/"
        f"{sink_archive.ARCHIVE_PREFIX}/{topic.stem}/{topic.stem}.parquet"
    )
    pmtiles_uri = (
        f"gs://{sink_pmtiles.PMTILES_BUCKET}/"
        f"{sink_pmtiles.PMTILES_PREFIX}/{topic.stem}/{topic.stem}.pmtiles"
    )
    ducklake_uri = f"{DATA_PATH.rstrip('/')}/{topic.schema}/{topic.stem}"

    item = {
        "type": "Feature",
        "stac_version": "1.0.0",
        "id": topic.stem,
        "geometry": geometry,
        "bbox": bbox,
        "properties": {
            "datetime": now,
            "ugs:dbt_schema": topic.schema,
            "ugs:layer": topic.layer,
            "ugs:row_count": count,
        },
        "assets": {
            "data": {
                "href": archive_uri,
                "type": "application/vnd.apache.parquet",
                "roles": ["data"],
                "title": "GeoParquet archive (native geometry)",
            },
            "pmtiles": {
                "href": pmtiles_uri,
                "type": "application/vnd.pmtiles",
                "roles": ["visual"],
                "title": "PMTiles vector tiles",
            },
            "ducklake": {
                "href": ducklake_uri,
                "type": "application/x-ducklake-table",
                "roles": ["data"],
                "title": "DuckLake table (native geometry)",
            },
        },
        "links": [],
    }

    with tempfile.TemporaryDirectory() as tmp:
        local = os.path.join(tmp, f"{topic.stem}.json")
        with open(local, "w") as f:
            json.dump(item, f, indent=2)
        store = GCSStore(bucket=STAC_BUCKET)
        gcs_object = f"{STAC_PREFIX}/{topic.stem}/{topic.stem}.json"
        obs.put(store, gcs_object, Path(local), attributes={"Content-Type": "application/json"})

    print(f"[{topic.fqn}] stac: gs://{STAC_BUCKET}/{gcs_object}")
