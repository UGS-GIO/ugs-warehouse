"""Build the statewide vector units PMTiles from UGS's authoritative seamless layer.

Pulls from the PostgREST seamlessgeolmap service, pages through features with adaptive
paging, converts EPSG:3857 coordinates to EPSG:4326, runs tippecanoe to compile the
vector tile layer, and uploads the final units.pmtiles to GCS.

    python -m ugs_warehouse.pubs.units_pmtiles
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile

import geopandas as gpd
import requests

from ..core import config, gcs
from . import identity, source

DEFAULT_POSTGREST = "https://postgrest-seamlessgeolmap-734948684426.us-central1.run.app"
POSTGREST = os.environ.get("POSTGREST_URL", DEFAULT_POSTGREST).rstrip("/")
TABLE = "seamlessgeolunits"
COLS = "unit_symbol,unit_name,age,scale,series_id,shape"

PMTILES_OBJECT = f"{identity.UNITS_PREFIX}/units.pmtiles"
PMTILES_MIME = config.PMTILES_MIME


def _fetch_features() -> list[dict]:
    """Adaptive paging: PostgREST/Cloud Run 500s when responses are too big.

    On failure, we halve the page size and retry instead of using a single fixed size.
    """
    s = requests.Session()
    s.headers["User-Agent"] = "ugs-warehouse-units-pmtiles"
    feats: list[dict] = []
    start = 0
    page = 1000

    while True:
        p = page
        while True:
            try:
                url = f"{POSTGREST}/{TABLE}"
                headers = {
                    "Accept": "application/geo+json",
                    "Range-Unit": "items",
                    "Range": f"{start}-{start + p - 1}",
                }
                r = s.get(url, params={"select": COLS, "is_current": "eq.Y"},
                          headers=headers, timeout=120)
                r.raise_for_status()
                break
            except Exception:
                if p <= 50:
                    raise
                p //= 2  # shrink range and retry
        got = r.json().get("features", [])
        feats.extend(got)
        if (start // 1000) % 25 == 0 or len(got) < p:
            print(f"  fetched {len(feats)} features (page size={p})")
        if len(got) < p:
            return feats
        start += len(got)
        page = min(1000, p * 2)  # recover page size


def _fetch_from_db(dsn: str) -> gpd.GeoDataFrame:
    """Fetch seamlessgeolunits directly from Postgres using DuckDB and convert to 4326."""
    import duckdb
    import geopandas as gpd
    from shapely import wkb

    print(f"Connecting to Postgres to fetch mapping.{TABLE}_current...")
    con = duckdb.connect()
    con.execute("INSTALL postgres; LOAD postgres; INSTALL spatial; LOAD spatial;")
    con.execute(f"ATTACH '{dsn}' AS db (TYPE POSTGRES, READ_ONLY)")

    query = f"SELECT {COLS[:-6]}, ST_AsWKB(shape) as geom_wkb FROM db.mapping.{TABLE}_current"
    df = con.execute(query).df()
    print(f"  fetched {len(df)} rows. Reconstructing geometries...")

    df["geometry"] = df["geom_wkb"].apply(lambda x: wkb.loads(bytes(x)) if x else None)
    df = df.dropna(subset=["geometry"])

    gdf = gpd.GeoDataFrame(df, geometry="geometry", crs="EPSG:3857")
    return gdf.to_crs(4326)


def build_units(minz: int = 4, maxz: int = 14) -> None:
    """Download, process, tile and upload the statewide units layer."""
    import geopandas as gpd

    dsn = (os.environ.get("POSTGRES_DSN") or
           os.environ.get("DUCKLAKE_CATALOG_DSN") or
           os.environ.get("PUBS_DB_URL"))

    is_pg = dsn and (dsn.startswith(("postgres://", "postgresql://")) or "host=" in dsn)

    if is_pg:
        dsn = source.inject_pg_password(dsn)
        gdf = _fetch_from_db(dsn)
    else:
        print(f"Pulling {TABLE} (is_current) from PostgREST: {POSTGREST}")
        feats = _fetch_features()
        print(f"{len(feats)} unit polygons -> converting EPSG:3857 -> EPSG:4326")
        gdf = gpd.GeoDataFrame.from_features(feats, crs="EPSG:3857").to_crs(4326)

    with tempfile.TemporaryDirectory() as tmp:
        geojsonl = os.path.join(tmp, "units.geojsonl")
        pmtiles = os.path.join(tmp, "units.pmtiles")
        gdf.to_file(geojsonl, driver="GeoJSONSeq")

        print("Running tippecanoe to compile vector PMTiles...")
        subprocess.run(
            [
                "tippecanoe",
                "-o", pmtiles,
                "-l", "units",
                "-n", "UGS seamless geologic units",
                "-Z", str(minz),
                "-z", str(maxz),
                "--coalesce-densest-as-needed",
                "--extend-zooms-if-still-dropping",
                "--maximum-tile-bytes=5000000",
                "--force",
                geojsonl,
            ],
            check=True,
        )

        print(f"Uploading {os.path.basename(pmtiles)} ({os.path.getsize(pmtiles)//1024//1024} MB) to GCS...")
        gcs.upload(pmtiles, PMTILES_OBJECT, content_type=PMTILES_MIME, cache_control=gcs.CACHE_MUTABLE)

    print(f"Statewide units PMTiles complete -> {config.public_url(PMTILES_OBJECT)}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Build statewide units vector PMTiles and upload to GCS")
    ap.add_argument("--minzoom", type=int, default=4, help="Min zoom for Tippecanoe (default 4)")
    ap.add_argument("--maxzoom", type=int, default=14, help="Max zoom for Tippecanoe (default 14)")
    args = ap.parse_args()

    build_units(minz=args.minzoom, maxz=args.maxzoom)
    return 0


if __name__ == "__main__":
    sys.exit(main())
