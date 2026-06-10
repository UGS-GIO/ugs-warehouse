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
CATALOG_ID = "ugs-warehouse"
# web-map-links extension — lets STAC Browser v4+ render the PMTiles layer on the
# item page (not just the footprint). https://github.com/stac-extensions/web-map-links
WEB_MAP_LINKS_EXT = "https://stac-extensions.github.io/web-map-links/v1.3.0/schema.json"
# Public base URL the data/pmtiles assets are served from. The raw bucket is private;
# the maps-assets CDN is the only public read surface, and it maps to the bucket's base
# folder with the object path preserved — so an object at `<prefix>/<file>` in the bucket
# is reachable at `https://maps-assets.geology.utah.gov/<prefix>/<file>`. Asset hrefs are
# therefore `{PUBLIC_BASE_URL}/{prefix}/{stem}/{file}`. Browsers/MapLibre can't fetch
# `gs://`, so these must be https. Override the env only for a different CDN/host.
PUBLIC_BASE_URL = os.environ.get(
    "WAREHOUSE_PUBLIC_BASE_URL",
    "https://maps-assets.geology.utah.gov",
).rstrip("/")


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
    if not PUBLIC_BASE_URL:
        raise RuntimeError(
            "WAREHOUSE_PUBLIC_BASE_URL not set — STAC asset hrefs need the public "
            "CDN base (the raw bucket is private). Set it to the maps-assets CDN."
        )
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
    # data + pmtiles served over https via the CDN so a browser/MapLibre can load them.
    # ducklake stays a gs:// locator — it's read by DuckDB, not a browser.
    archive_uri = (
        f"{PUBLIC_BASE_URL}/{sink_archive.ARCHIVE_PREFIX}/{topic.stem}/{topic.stem}.parquet"
    )
    pmtiles_uri = (
        f"{PUBLIC_BASE_URL}/{sink_pmtiles.PMTILES_PREFIX}/{topic.stem}/{topic.stem}.pmtiles"
    )
    ducklake_uri = f"{DATA_PATH.rstrip('/')}/{topic.schema}/{topic.stem}"

    item = {
        "type": "Feature",
        "stac_version": "1.0.0",
        "stac_extensions": [WEB_MAP_LINKS_EXT],
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
        "links": [
            {"rel": "root", "href": "../catalog.json", "type": "application/json"},
            {"rel": "parent", "href": "../catalog.json", "type": "application/json"},
            {"rel": "self", "href": f"./{topic.stem}.json", "type": "application/geo+json"},
            # web-map-links: makes STAC Browser v4+ render the actual layer on the item
            # page (default/unstyled), not just the footprint. Source-layer name is the
            # topic stem (tippecanoe `-l {stem}` in sink_pmtiles).
            {
                "rel": "pmtiles",
                "href": pmtiles_uri,
                "type": "application/vnd.pmtiles",
                "pmtiles:layers": [topic.stem],
            },
        ],
    }

    with tempfile.TemporaryDirectory() as tmp:
        local = os.path.join(tmp, f"{topic.stem}.json")
        with open(local, "w") as f:
            json.dump(item, f, indent=2)
        store = GCSStore(bucket=STAC_BUCKET)
        gcs_object = f"{STAC_PREFIX}/{topic.stem}/{topic.stem}.json"
        obs.put(store, gcs_object, Path(local), attributes={"Content-Type": "application/json"})

    print(f"[{topic.fqn}] stac: gs://{STAC_BUCKET}/{gcs_object}")


def _item_hrefs(paths: list[str]) -> list[str]:
    """Catalog-relative item hrefs from GCS object paths under STAC_PREFIX.

    Items live one dir down as `<stem>/<stem>.json`; the root `catalog.json`
    and any other top-level files are skipped. Sorted for stable output.
    """
    hrefs = []
    for path in paths:
        if not path.endswith(".json"):
            continue
        rel = path[len(STAC_PREFIX):].lstrip("/")
        if "/" not in rel:  # top-level file (e.g. catalog.json) — not an item
            continue
        hrefs.append(f"./{rel}")
    return sorted(set(hrefs))


def _catalog_doc(item_hrefs: list[str]) -> dict:
    """Build the static root STAC Catalog linking every item."""
    return {
        "type": "Catalog",
        "stac_version": "1.0.0",
        "id": CATALOG_ID,
        "description": "UGS warehouse serving catalog — one STAC item per topic, "
                       "each linking its GeoParquet, PMTiles, and DuckLake artifacts.",
        "links": [
            {"rel": "root", "href": "./catalog.json", "type": "application/json"},
            {"rel": "self", "href": "./catalog.json", "type": "application/json"},
            *[{"rel": "item", "href": h, "type": "application/geo+json"} for h in item_hrefs],
        ],
    }


def refresh_catalog() -> None:
    """Rebuild the static root catalog by listing item files in GCS.

    Derive-from-truth: lists every `<stem>/<stem>.json` under STAC_PREFIX and
    rewrites `catalog.json`. Called automatically after each ingest, so the
    catalog stays current with no manual regen step. Idempotent; under
    concurrent ingests the last writer wins (brief staleness, self-heals next
    ingest).
    """
    store = GCSStore(bucket=STAC_BUCKET)
    paths: list[str] = []
    for batch in obs.list(store, prefix=STAC_PREFIX):
        paths.extend(m["path"] for m in batch)

    hrefs = _item_hrefs(paths)
    with tempfile.TemporaryDirectory() as tmp:
        local = os.path.join(tmp, "catalog.json")
        with open(local, "w") as f:
            json.dump(_catalog_doc(hrefs), f, indent=2)
        obs.put(store, f"{STAC_PREFIX}/catalog.json", Path(local),
                attributes={"Content-Type": "application/json"})

    print(f"[catalog] stac: gs://{STAC_BUCKET}/{STAC_PREFIX}/catalog.json ({len(hrefs)} items)")
