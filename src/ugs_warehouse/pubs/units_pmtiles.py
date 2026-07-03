"""Build the statewide vector units PMTiles from UGS's authoritative seamless layer.

Pulls from the PostgREST seamlessgeolmap service, pages through features with adaptive
paging, converts EPSG:3857 coordinates to EPSG:4326, runs tippecanoe to compile the
vector tile layers, and uploads the final units.pmtiles to GCS.

Mirrors the old geolMapPortal's scale tiers: the source `scale` column splits the units into three
independent layers in the one PMTiles — `units_small` (~1:500k), `units_intermediate` (~1:100k),
`units_large` (~1:24k). All three tile the full zoom range (visible at every zoom; the viewer
toggles them). Tiling is lossless-as-possible — no feature dropping or geometry simplification —
so files are large, but this is regenerated only every few months.

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
# Full-fidelity seamless units as GeoParquet alongside the tiles — the lossless source of truth for
# download / analysis / DuckDB, paired with the .pmtiles (render) on one STAC item. Same pattern as
# footprints.py. Because this holds every vertex, the tiles can simplify low zooms freely.
PARQUET_OBJECT = f"{identity.UNITS_PREFIX}/units.parquet"
PARQUET_MIME = config.PARQUET_MIME


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


# The seamless source partitions every unit polygon into one of three cartographic scale tiers
# (the `scale` column). These become three independent vector-tile layers in the one PMTiles, each
# tiled across the FULL zoom range so all three are visible at every zoom — the viewer toggles them
# (NOT zoom-banded). Anything with an unexpected/missing scale falls into the mid tier, so no unit
# is ever dropped. small = coarse (~1:500k), intermediate = ~1:100k (the bulk), large = ~1:24k detail.
SCALE_LAYERS = {"small": "units_small", "intermediate": "units_intermediate", "large": "units_large"}
DEFAULT_TIER = "intermediate"


def build_units(minz: int = 0, maxz: int = 14) -> None:
    """Download, process, tile and upload the statewide units layer (3 scale-tier sub-layers)."""
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

    # Route every feature to a tier; unknown/missing scale -> mid tier so nothing is lost.
    tier = gdf["scale"].where(gdf["scale"].isin(SCALE_LAYERS), DEFAULT_TIER)

    with tempfile.TemporaryDirectory() as tmp:
        # Lossless source of truth: the full seamless units as GeoParquet (every vertex + attribute).
        # write_covering_bbox=True emits the GeoParquet 1.1 `covering` bbox struct so spec-aware
        # readers (GDAL/pyarrow) prune row groups spatially without decoding geometry.
        parquet = os.path.join(tmp, "units.parquet")
        gdf.to_parquet(parquet, write_covering_bbox=True)
        print(f"Uploading units.parquet ({os.path.getsize(parquet)//1024//1024} MB) to GCS...")
        gcs.upload(parquet, PARQUET_OBJECT, content_type=PARQUET_MIME, cache_control=gcs.CACHE_MUTABLE)

        pmtiles = os.path.join(tmp, "units.pmtiles")
        layer_args: list[str] = []
        for scale_val, layer_name in SCALE_LAYERS.items():
            sub = gdf[tier == scale_val]
            path = os.path.join(tmp, f"{layer_name}.geojsonl")
            if len(sub):
                sub.to_file(path, driver="GeoJSONSeq")
            else:  # empty layer still declared so the schema is stable across regens
                open(path, "w").close()
            print(f"  {layer_name}: {len(sub)} polygons")
            layer_args += ["-L", f"{layer_name}:{path}"]

        # Keep every feature at every zoom (no dropping, no tiny-polygon/density reduction). Geometry
        # IS simplified at low zoom (no -ps) — invisible when zoomed out, and tippecanoe always keeps
        # full resolution at maxzoom; the lossless full geometry also lives in units.parquet above.
        # So low-zoom tiles stay small while detail + the source of truth are preserved.
        print("Running tippecanoe to compile vector PMTiles (3 scale layers)...")
        subprocess.run(
            [
                "tippecanoe",
                "-o", pmtiles,
                "-n", "UGS seamless geologic units",
                "-Z", str(minz),
                "-z", str(maxz),
                "--no-feature-dropping",        # keep every unit at every zoom (-pf)
                "--no-tiny-polygon-reduction",  # keep slivers (-pt)
                "-r1",                           # no density-based thinning
                "--maximum-tile-bytes=30000000",
                "--extend-zooms-if-still-dropping",
                "--force",
                *layer_args,
            ],
            check=True,
        )

        print(f"Uploading {os.path.basename(pmtiles)} ({os.path.getsize(pmtiles)//1024//1024} MB) to GCS...")
        gcs.upload(pmtiles, PMTILES_OBJECT, content_type=PMTILES_MIME, cache_control=gcs.CACHE_MUTABLE)

    print(f"Statewide units PMTiles complete -> {config.public_url(PMTILES_OBJECT)}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Build statewide units vector PMTiles and upload to GCS")
    ap.add_argument("--minzoom", type=int, default=0, help="Min zoom for Tippecanoe (default 0 — visible at all scales)")
    ap.add_argument("--maxzoom", type=int, default=14, help="Max zoom for Tippecanoe (default 14)")
    args = ap.parse_args()

    build_units(minz=args.minzoom, maxz=args.maxzoom)
    return 0


if __name__ == "__main__":
    sys.exit(main())
