"""Harvest missing 24k geologic quads directly from the legacy ArcGIS ImageServer.

Extracts pre-georeferenced rasters from webmaps.geology.utah.gov/arcgis/rest/services/GeolMap/MD_24K
using exportImage, clips them with the neatline cutline from Geologic_Map_Footprints_View,
and builds archival Cloud-Optimized GeoTIFFs (DEFLATE + internal overviews) in GCS.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

from PIL import Image

from ugs_warehouse.core import config, gcs
from ugs_warehouse.pubs import identity

MD_24K_URL = "https://webmaps.geology.utah.gov/arcgis/rest/services/GeolMap/MD_24K/ImageServer"
FOOTPRINTS_URL = (
    "https://services.arcgis.com/ZzrwjTRez6FJiOq4/ArcGIS/rest/services/"
    "Geologic_Map_Footprints_View/FeatureServer/0/query"
)

# Standard Web Mercator resolution for zoom 16 native 24k capture (~2.388 m/px)
TARGET_RES = 2.388657


def fetch_missing_footprints() -> list[dict]:
    """Fetch 24k footprint features whose COG is not already in GCS."""
    print("[legacy-harvest] Listing existing COGs in GCS...")
    existing = set()
    for name in gcs.list_paths(f"{identity.COG_PREFIX}/"):
        if name.endswith(".cog.tif"):
            sid = name[len(f"{identity.COG_PREFIX}/") : -len(".cog.tif")].upper()
            existing.add(sid)
    print(f"[legacy-harvest] Found {len(existing)} existing COGs in GCS")

    print("[legacy-harvest] Fetching 24k footprints from ArcGIS Online...")
    url = (
        f"{FOOTPRINTS_URL}?where=geomaps_service='geomaps_24k'"
        "&outFields=series_id,quad_name,pub_year&returnGeometry=true&outSR=3857&f=geojson"
        "&resultRecordCount=2000"
    )
    with urllib.request.urlopen(url, timeout=60) as r:
        gj = json.load(r)

    missing = []
    seen = set()
    for feat in gj.get("features", []):
        sid = (feat.get("properties", {}).get("series_id") or "").strip().upper()
        if not sid or sid in existing or sid in seen:
            continue
        seen.add(sid)
        missing.append(feat)

    print(f"[legacy-harvest] Identified {len(missing)} missing 24k map footprints")
    return missing


def fetch_json(url: str, retries: int = 3, timeout: int = 60) -> dict:
    """Fetch JSON with retry and exponential backoff."""
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                return json.load(r)
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(2 * (attempt + 1))
    return {}


def check_legacy_data(feat: dict) -> tuple[bool, str]:
    """Check if legacy MD_24K has data at the center of this quad."""
    geom = feat.get("geometry", {})
    rings = geom.get("coordinates", [[]])[0]
    if not rings:
        return False, "no geometry"
    xs = [p[0] for p in rings]
    ys = [p[1] for p in rings]
    cx = sum(xs) / len(xs)
    cy = sum(ys) / len(ys)

    id_url = (
        f"{MD_24K_URL}/identify?geometry={cx:.1f},{cy:.1f}"
        "&geometryType=esriGeometryPoint&spatialReference=3857"
        "&returnGeometry=false&returnCatalogItems=true&f=json"
    )
    try:
        res = fetch_json(id_url, retries=2, timeout=15)
        items = res.get("catalogItems", {}).get("features", [])
        val = res.get("value", "")
        if items and val != "NoData" and val != "":
            return True, items[0]["attributes"].get("Name", "")
        return False, "NoData"
    except Exception as e:
        return False, f"error: {e}"


def export_quad_cog(feat: dict, sid: str, quad: str, work_dir: str) -> str | None:
    """Export top and bottom halves from MD_24K, merge and cutline-warp to COG."""
    geom = feat.get("geometry", {})
    rings = geom.get("coordinates", [[]])[0]
    xs = [p[0] for p in rings]
    ys = [p[1] for p in rings]
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    ymid = (ymin + ymax) / 2.0

    w_px = int((xmax - xmin) / TARGET_RES)
    h_px_half = int((ymax - ymid) / TARGET_RES)

    top_url = (
        f"{MD_24K_URL}/exportImage?bbox={xmin:.1f},{ymid:.1f},{xmax:.1f},{ymax:.1f}"
        f"&bboxSR=3857&imageSR=3857&size={w_px},{h_px_half}&format=tiff&f=json"
    )
    bot_url = (
        f"{MD_24K_URL}/exportImage?bbox={xmin:.1f},{ymin:.1f},{xmax:.1f},{ymid:.1f}"
        f"&bboxSR=3857&imageSR=3857&size={w_px},{h_px_half}&format=tiff&f=json"
    )

    top_res = fetch_json(top_url, retries=3, timeout=60)
    bot_res = fetch_json(bot_url, retries=3, timeout=60)

    if not top_res.get("href") or not bot_res.get("href"):
        return None

    top_tif = os.path.join(work_dir, "top.tif")
    bot_tif = os.path.join(work_dir, "bot.tif")
    cutline = os.path.join(work_dir, "cutline.geojson")
    out_cog = os.path.join(work_dir, f"{sid}.cog.tif")

    with open(cutline, "w") as f:
        json.dump({"type": "FeatureCollection", "features": [feat]}, f)

    urllib.request.urlretrieve(top_res["href"], top_tif)
    urllib.request.urlretrieve(bot_res["href"], bot_tif)

    vrt = os.path.join(work_dir, "merged.vrt")
    subprocess.run(["gdalbuildvrt", "-q", vrt, top_tif, bot_tif], check=True)

    warped_vrt = os.path.join(work_dir, "warped.vrt")
    subprocess.run(
        [
            "gdalwarp",
            "-q",
            "-cutline",
            cutline,
            "-cutline_srs",
            "EPSG:3857",
            "-crop_to_cutline",
            "-dstalpha",
            "-t_srs",
            "EPSG:3857",
            "-of",
            "VRT",
            vrt,
            warped_vrt,
        ],
        check=True,
    )

    subprocess.run(
        [
            "gdal_translate",
            "-q",
            "-of",
            "COG",
            "-co",
            "COMPRESS=DEFLATE",
            "-co",
            "PREDICTOR=2",
            "-co",
            "OVERVIEW_RESAMPLING=average",
            "-co",
            "NUM_THREADS=ALL_CPUS",
            warped_vrt,
            out_cog,
        ],
        check=True,
    )

    return out_cog


def make_thumbnail(cog_path: str, thumb_path: str) -> None:
    """Generate a 512px WebP thumbnail from the COG."""
    import rasterio

    with rasterio.open(cog_path) as ds:
        # Read overview or downsampled image
        ov_idx = len(ds.overviews(1)) - 1
        scale_factor = ds.overviews(1)[ov_idx] if ov_idx >= 0 else 16
        out_w = max(1, ds.width // scale_factor)
        out_h = max(1, ds.height // scale_factor)
        data = ds.read(out_shape=(ds.count, out_h, out_w))

    if data.shape[0] >= 4:
        im = Image.fromarray(data[:4].transpose((1, 2, 0)), mode="RGBA")
    else:
        im = Image.fromarray(data[:3].transpose((1, 2, 0)), mode="RGB")
    im.thumbnail((512, 512))
    im.save(thumb_path, "WEBP", quality=85)


def main() -> None:
    missing = fetch_missing_footprints()
    if not missing:
        print("[legacy-harvest] No missing footprints. Complete!")
        return

    recovered = 0
    skipped = 0

    for i, feat in enumerate(missing, 1):
        sid = feat["properties"]["series_id"].strip()
        quad = feat["properties"].get("quad_name", "")
        has_data, name_or_reason = check_legacy_data(feat)

        if not has_data:
            print(f"[{i}/{len(missing)}] SKIP {sid:<12} ({quad:<25}) -> {name_or_reason}")
            skipped += 1
            continue

        print(f"[{i}/{len(missing)}] HARVEST {sid:<12} ({quad:<25}) -> \"{name_or_reason}\"")
        t0 = time.time()
        with tempfile.TemporaryDirectory() as td:
            try:
                cog = export_quad_cog(feat, sid, quad, td)
                if not cog or not os.path.exists(cog):
                    print(f"  FAILED {sid}: export produced no raster")
                    continue

                thumb = os.path.join(td, f"{sid}.thumb.webp")
                make_thumbnail(cog, thumb)

                cog_obj = f"{identity.COG_PREFIX}/{sid}.cog.tif"
                thumb_obj = f"{identity.COG_PREFIX}/{sid}.thumb.webp"

                gcs.upload(cog, cog_obj, content_type=config.COG_MIME)
                gcs.upload(thumb, thumb_obj, content_type="image/webp")

                mb = os.path.getsize(cog) / 1024 / 1024
                elapsed = time.time() - t0
                print(f"  OK {sid} -> {mb:.1f} MB in {elapsed:.1f}s uploaded to {cog_obj}", flush=True)
                recovered += 1
                time.sleep(0.5)
            except Exception as e:
                print(f"  ERROR {sid}: {e}", file=sys.stderr, flush=True)

    print("\n" + "=" * 60)
    print(f"[legacy-harvest] Complete: {recovered} recovered, {skipped} skipped.")


if __name__ == "__main__":
    main()
