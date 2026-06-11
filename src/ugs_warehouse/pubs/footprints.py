"""Footprints — the Geologic_Map_Footprints_View as GeoParquet + a vector PMTiles coverage
layer, and the `geoms()` reader the pub STAC build uses for per-map geometry.

Ported from ugs-geolmap-cog-poc/pipeline/export_footprints.py, output adapted to `core.gcs`.
The FeatureServer returns GeoJSON in EPSG:4326 (tippecanoe-ready, no reproject). The
COG-bounds fallback (extents for harvested maps the view lacks) is dropped for now — COGs
live in GCS, so deriving their bounds is a follow-up; view footprints cover most maps.

`export()` needs the `pubs` extra (geopandas) + the tippecanoe binary (Dockerfile.harvest).
"""
from __future__ import annotations

import os
import subprocess
import tempfile

import requests

from ..core import config, gcs
from . import identity, source, topic

FOOTPRINTS_URL = ("https://services.arcgis.com/ZzrwjTRez6FJiOq4/ArcGIS/rest/services/"
                  "Geologic_Map_Footprints_View/FeatureServer/0/query")
PARQUET_OBJECT = f"{identity.FOOTPRINTS_PREFIX}/footprints.parquet"
PMTILES_OBJECT = f"{identity.FOOTPRINTS_PREFIX}/footprints.pmtiles"
PARQUET_MIME = "application/vnd.apache.parquet"
PMTILES_MIME = "application/vnd.pmtiles"


def _fetch() -> list[dict]:
    s = requests.Session()
    feats: list[dict] = []
    off = 0
    while True:
        gj = s.get(FOOTPRINTS_URL, params={
            "where": "1=1", "outFields": "*", "returnGeometry": "true", "outSR": "4326",
            "f": "geojson", "resultOffset": off, "resultRecordCount": 1000}, timeout=120).json()
        page = gj.get("features", [])
        feats.extend(page)
        print(f"  fetched {len(feats)}")
        if not page or (not gj.get("exceededTransferLimit") and len(page) < 1000):
            return feats
        off += len(page)


def export(minz: int = 2, maxz: int = 11) -> None:
    """Fetch the view -> GeoParquet + footprints PMTiles, uploaded to GCS."""
    import geopandas as gpd

    feats = _fetch()
    gdf = gpd.GeoDataFrame.from_features(feats, crs="EPSG:4326")
    gdf = gdf[~gdf.geometry.is_empty & gdf.geometry.notna()]
    gdf["footprint_source"] = "view"
    # subject topic per map (same classifier the STAC items use, so footprint.topic == ugs:topic)
    meta = {str(p["series_id"]).upper(): p for p in source.read_pubs()}
    gdf["topic"] = [
        topic.classify(meta.get(str(s).upper(), {}).get("pub_name"),
                       meta.get(str(s).upper(), {}).get("keywords"))
        for s in gdf["series_id"]
    ]
    print(f"{len(gdf)} footprints from view")

    with tempfile.TemporaryDirectory() as tmp:
        parquet = os.path.join(tmp, "footprints.parquet")
        geojsonl = os.path.join(tmp, "footprints.geojsonl")
        pmtiles = os.path.join(tmp, "footprints.pmtiles")
        gdf.to_parquet(parquet)
        gdf.to_file(geojsonl, driver="GeoJSONSeq")
        subprocess.run(["tippecanoe", "-o", pmtiles, "-l", "footprints", "-n", "UGS map footprints",
                        "-Z", str(minz), "-z", str(maxz), "--drop-densest-as-needed", "--force",
                        geojsonl], check=True)
        gcs.upload(parquet, PARQUET_OBJECT, content_type=PARQUET_MIME,
                   cache_control=gcs.CACHE_MUTABLE)
        gcs.upload(pmtiles, PMTILES_OBJECT, content_type=PMTILES_MIME,
                   cache_control=gcs.CACHE_MUTABLE)
    print(f"footprints -> {config.public_url(PARQUET_OBJECT)} + {config.public_url(PMTILES_OBJECT)}")


def geoms() -> dict[str, tuple]:
    """series_id -> (geometry GeoJSON, bbox, source). Reads footprints.parquet from GCS; one
    dissolved geometry per pub. Raises FileNotFoundError if the parquet hasn't been exported
    (the pub STAC build then falls back to null geometry)."""
    import geopandas as gpd

    data = gcs.get_bytes(PARQUET_OBJECT)  # raises if absent
    with tempfile.TemporaryDirectory() as tmp:
        local = os.path.join(tmp, "footprints.parquet")
        with open(local, "wb") as f:
            f.write(data)
        g = gpd.read_parquet(local)
    if "footprint_source" not in g.columns:
        g["footprint_source"] = "view"
    g = g[["series_id", "geometry", "footprint_source"]].dropna(subset=["geometry"])
    g = g.dissolve(by="series_id", aggfunc={"footprint_source": "first"})
    out: dict[str, tuple] = {}
    for sid, row in g.iterrows():
        geom = row.geometry
        out[str(sid).upper()] = (geom.__geo_interface__, list(geom.bounds), row.footprint_source)
    return out
