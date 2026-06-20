"""Harvest one UGS geologic-map publication -> clipped, reprojected, validated COG.

Ported from ugs-geolmap-cog-poc/pipeline/harvest.py. The GDAL processing (download zip ->
prepare plate (PDF@DPI or GeoTIFF) -> gdalwarp cutline+reproject 3857 -> rio-cogeo COG ->
validate) is kept verbatim — it's the proven core. Only the IO layer changed: artifacts
upload to the warehouse bucket via `core.gcs` (obstore/ADC), object-path namespaced by the
pubs prefixes. POC-only bits (local dir, fake-gcs emulator, chmod, thermal cooldown, file
failure log) are dropped — Cloud Run is GCS-only and logs to stderr.

Needs the `pubs` extra (rasterio, rio-cogeo) + system GDAL CLI + poppler (the Dockerfile.harvest
image). Env: COG_COMPRESS (webp), COG_QUALITY (90), COG_DPI (600; 0 = GeoTIFF as-is),
SKIP_EXISTING (1), THUMBS (1).
"""
from __future__ import annotations

import csv
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from urllib.parse import quote, urlsplit, urlunsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ..core import gcs
from . import identity

FOOTPRINTS = ("https://services.arcgis.com/ZzrwjTRez6FJiOq4/ArcGIS/rest/services/"
              "Geologic_Map_Footprints_View/FeatureServer/0/query")
DATAPHP = "https://geology.utah.gov/apps/pubs_landing/data.php"
# Optional pre-built manifest (inventory.py); else fall back to data.php per series.
MANIFEST = os.environ.get("GEOLMAP_MANIFEST", "")
SKIP_EXISTING = os.environ.get("SKIP_EXISTING", "1") != "0"
THUMBS = os.environ.get("THUMBS", "1") != "0"
COG_COMPRESS = os.environ.get("COG_COMPRESS", "webp").lower()
COG_QUALITY = int(os.environ.get("COG_QUALITY", "90"))
COG_DPI = int(os.environ.get("COG_DPI", "600"))

COG_MIME = "image/tiff; application=geotiff; profile=cloud-optimized"
PARQUET_MIME = "application/vnd.apache.parquet"

S = requests.Session()
S.headers["User-Agent"] = "ugs-warehouse-pubs"
# The footprint FeatureServer rate-limits under batch load and drops connections; back off.
_retry = Retry(total=6, connect=6, read=6, backoff_factor=1.5,
               status_forcelist=[429, 500, 502, 503, 504], allowed_methods=["GET"])
S.mount("https://", HTTPAdapter(max_retries=_retry))
S.mount("http://", HTTPAdapter(max_retries=_retry))


def _get(url, params):
    r = S.get(url, params=params, timeout=120)
    r.raise_for_status()
    return r.json()


def run(cmd):
    # capture so failures surface the real gdal error, not just "exit 1"
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def encode_url(u):
    """URL-encode the path (raw spaces/brackets break requests); keep existing %xx."""
    s = urlsplit(u)
    return urlunsplit((s.scheme, s.netloc, quote(s.path, safe="/%"), s.query, s.fragment))


def download(url, dest):
    with S.get(url, stream=True, timeout=900) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for c in r.iter_content(1 << 20):
                f.write(c)


def pick(names, *pats):
    for p in pats:
        h = [n for n in names if re.search(p, n, re.I)]
        if h:
            return h[0]
    return None


def manifest_urls(series_id):
    """(geotiff_zip_url, gis_zip_url) from the optional manifest, if present."""
    if MANIFEST and os.path.exists(MANIFEST):
        for r in csv.DictReader(open(MANIFEST)):
            if r["series_id"].strip().upper() == series_id.upper():
                return (r.get("geotiff_zip_url") or None, r.get("gis_zip_url") or None)
    return None, None


def data_php_urls(series_id):
    gt = gis = None
    for k, v in (_get(DATAPHP, {"pub": series_id}).get("downloads") or {}).items():
        if not str(v).lower().endswith(".zip"):
            continue
        if "geotiff" in k.lower():
            gt = v
        elif "gis" in k.lower():
            gis = v
    return gt, gis


def footprint(series_id, work):
    # exact match — LIKE '%id%' substring-matches (I-2 -> I-20 …) and the unioned cutline
    # spans the whole state, exploding the warp output to billions of px.
    gj = _get(FOOTPRINTS, {"where": f"series_id = '{series_id}'", "outFields": "series_id",
                           "returnGeometry": "true", "outSR": "4326", "f": "geojson"})
    p = os.path.join(work, "cutline.geojson")
    json.dump(gj, open(p, "w"))
    return p, len(gj.get("features", []))


def _aux_srs(aux_path):
    """Real CRS from an ESRI .aux.xml — the AUTHORITY EPSG of the last ProjectedCoordinateSystem
    WKT (the AdjustXform carries the true projection)."""
    txt = open(aux_path, encoding="utf-8", errors="replace").read()
    wkts = re.findall(r"<WKT>(.*?)</WKT>", txt, re.S)
    for w in reversed(wkts):
        w = w.replace("&quot;", '"')
        m = re.findall(r'AUTHORITY\["EPSG",\s*"?(\d+)"?\]', w)
        if m:
            return f"EPSG:{m[-1]}"
    return None


def _prj_srs(work):
    """CRS from any sibling .prj in the bundle — GIS bundles ship .prj for shapefiles but often
    not for the base raster, yet the raster's world-file coords are in that same project CRS."""
    import glob

    from rasterio.crs import CRS
    for p in sorted(glob.glob(os.path.join(work, "**", "*.prj"), recursive=True)):
        try:
            c = CRS.from_wkt(open(p).read())
            return f"EPSG:{c.to_epsg()}" if c.to_epsg() else c.to_wkt()
        except Exception:
            continue
    return None


def _is_unreferenced(tif):
    """True when the embedded CRS can't be warped to 3857 (engineering/unknown/none) — the real
    georef then lives in sidecar world file + .aux.xml."""
    import rasterio
    try:
        with rasterio.open(tif) as ds:
            crs = ds.crs
    except Exception:
        return False
    if crs is None:
        return True
    if crs.is_projected or crs.is_geographic:
        return False
    return True


def corrected_georef(gtif, work):
    """If the GeoTIFF's embedded CRS is unusable, rebuild a VRT whose SRS comes from the .aux.xml
    and whose geotransform comes from the world file. Returns the VRT path, or the original gtif
    when the embedded CRS is fine / no sidecars exist."""
    if not _is_unreferenced(gtif):
        return gtif
    stem = re.sub(r"\.[^.]+$", "", gtif)
    wf = next((stem + e for e in (".tfwx", ".tfw", ".wld") if os.path.exists(stem + e)), None)
    aux = gtif + ".aux.xml" if os.path.exists(gtif + ".aux.xml") else None
    srs = _aux_srs(aux) if aux else None
    if not srs:
        srs = _prj_srs(work)
        if srs:
            print(f"  georef SRS from bundle .prj: {srs}")
    if not (wf and srs):
        print(f"  WARN georef sidecars missing (wf={bool(wf)} srs={bool(srs)}) "
              f"for {os.path.basename(gtif)}")
        return gtif
    A, D, B, E, C, F = [float(x) for x in open(wf).read().split()[:6]]
    gt = (C - 0.5 * A - 0.5 * B, A, B, F - 0.5 * D - 0.5 * E, D, E)
    vrt = stem + ".fixed.vrt"
    run(["gdal_translate", "-q", "-of", "VRT", gtif, vrt])
    xml = open(vrt).read()
    xml = re.sub(r"<SRS[^>]*>.*?</SRS>", f"<SRS>{srs}</SRS>", xml, flags=re.S)
    gtx = "<GeoTransform>%.12g, %.12g, %.12g, %.12g, %.12g, %.12g</GeoTransform>" % gt
    xml = (re.sub(r"<GeoTransform>.*?</GeoTransform>", gtx, xml, flags=re.S)
           if "<GeoTransform>" in xml else xml.replace("</VRTDataset>", gtx + "</VRTDataset>"))
    open(vrt, "w").write(xml)
    print(f"  georef rebuilt from sidecars: {srs} + {os.path.basename(wf)}")
    return vrt


def ensure_rgb(tif):
    """WEBP + gdalbuildvrt need Red/Green/Blue(/Alpha) color interpretation. Expand palette plates;
    relabel or replicate grayscale plates so they mosaic cleanly."""
    import rasterio
    from rasterio.enums import ColorInterp
    with rasterio.open(tif) as ds:
        n, ci = ds.count, ds.colorinterp
    if ColorInterp.palette in ci:
        out = tif + ".rgb.tif"
        run(["gdal_translate", "-expand", "rgba", tif, out])
        return out
    if ci[:3] == (ColorInterp.red, ColorInterp.green, ColorInterp.blue):
        return tif
    if ColorInterp.gray in ci:
        out = tif + ".rgb.tif"
        if n >= 3:
            ci_list = "red,green,blue" + (",alpha" if n >= 4 else "")
            run(["gdal_translate", "-colorinterp", ci_list, tif, out])
        else:
            args = ["-b", "1", "-b", "1", "-b", "1"] + (["-b", "2"] if n == 2 else [])
            ci_list = "red,green,blue" + (",alpha" if n == 2 else "")
            run(["gdal_translate"] + args + ["-colorinterp", ci_list, tif, out])
        return out
    return tif


def prepare_plates(zip_paths, work):
    """Extract plates from multiple zips. If COG_DPI>0 and a geospatial PDF is present, rasterize
    it at COG_DPI and georeference from the GeoTIFF; else use the published GeoTIFF. Returns
    (plate_path_or_vrt, shapefile_path_or_None)."""
    import rasterio
    gtif = pdf = shp = None
    for zip_path in zip_paths:
        with zipfile.ZipFile(zip_path) as z:
            names = z.namelist()
            _gtif = (pick(names, r"plate1.*geotiff\.tiff?$", r"geotiff\.tiff?$",
                          r"utah-500k.*\.tiff?$")
                     or next((n for n in names if n.lower().endswith((".tif", ".tiff")) and not any(
                         x in n.lower() for x in ("basemap", "topo", "hillshade", "mashup"))), None))
            _pdf = pick(names, r"plate1.*geospatial\.pdf$", r"geospatial\.pdf$",
                        r"GeologicMapOfUtah_plate1\.pdf$")
            _shp = pick(names, r"geologicunits\.shp$", r"units\.shp$")
            if _gtif:
                gtif = _gtif
            if _pdf:
                pdf = _pdf
            if _shp:
                shp = _shp
            want = [n for n in (_gtif, _pdf) if n]
            if _gtif:
                gstem = re.sub(r"\.[^.]+$", "", _gtif)
                want += [n for n in names if n.startswith(gstem + ".")
                         and n.lower().endswith((".tfwx", ".tfw", ".wld", ".aux.xml"))]
                _prj = next((n for n in names if n.lower().endswith(".prj")), None)
                if _prj:
                    want.append(_prj)
            if _shp:
                stem = re.sub(r"\.shp$", "", _shp, flags=re.I)
                want += [n for n in names if re.sub(r"\.[^.]+$", "", n) == stem]
            for nm in want:
                z.extract(nm, work)

    shp_path = os.path.join(work, shp) if shp else None
    if not gtif:
        return None, shp_path
    gtif = corrected_georef(os.path.join(work, gtif), work)
    if COG_DPI > 0 and pdf:
        prefix = os.path.join(work, "plate")
        run(["pdftoppm", "-png", "-r", str(COG_DPI), os.path.join(work, pdf), prefix])
        png = next((p for p in (prefix + "-1.png", prefix + ".png") if os.path.exists(p)), None)
        with rasterio.open(gtif) as g:
            b, crs = g.bounds, g.crs
        if png and crs is not None:
            srs = os.path.join(work, "srs.wkt")
            open(srs, "w").write(crs.to_wkt())
            vrt = os.path.join(work, "plate.vrt")
            run(["gdal_translate", "-q", "-of", "VRT", "-a_srs", srs, "-a_ullr",
                 str(b.left), str(b.top), str(b.right), str(b.bottom), png, vrt])
            return vrt, shp_path
    return gtif, shp_path


def harvest_one(series_id: str, dry_run: bool = False, force: bool = False) -> str:
    """Harvest a series_id -> COG (+ units parquet, thumbnail) in GCS. Returns ok|skip|fail:*."""
    pub = identity.Pub.parse(series_id)
    series_id = pub.series_id
    if "XXXX" in series_id:
        print(f"{series_id}: SKIP (unpublished placeholder)")
        return "skip"
    skip_existing = SKIP_EXISTING and not force and not dry_run
    if skip_existing and gcs.exists(pub.cog_object):
        print(f"{series_id}: SKIP (exists)")
        return "skip"
    gt_url, gis_url = manifest_urls(series_id)
    if not gt_url and not gis_url:
        try:
            gt_url, gis_url = data_php_urls(series_id)
        except Exception:
            gt_url, gis_url = None, None

    if dry_run:
        print(f"[dry-run] {series_id}: URLs: gt={gt_url}, gis={gis_url}")
        return "ok"

    # high-DPI needs the GIS bundle (carries the geospatial PDF); else the lighter GeoTiff-Zip
    zurls = []
    if COG_DPI > 0 and gis_url and gt_url:
        zurls = [gt_url, gis_url]
    else:
        zurl = (gis_url or gt_url) if COG_DPI > 0 else (gt_url or gis_url)
        if zurl:
            zurls = [zurl]
    if not zurls:
        print(f"{series_id}: FAIL no zip (PDF-only?)", file=sys.stderr)
        return "fail:nozip"

    status = _harvest_attempt(pub, zurls)
    # FALLBACK: the GIS bundle's base raster sometimes has no usable SRS -> the cutline warp dies.
    # The separate GeoTIFF-Zip is the clean georeferenced plate; retry with it alone.
    if status.startswith("fail") and gt_url and zurls != [gt_url]:
        print(f"{series_id}: GIS source failed -> retry GeoTIFF source only")
        if _harvest_attempt(pub, [gt_url]) == "ok":
            return "ok"
    return status


def _harvest_attempt(pub: identity.Pub, zurls) -> str:
    """One harvest attempt over a set of source zip URL(s). Returns 'ok' | 'fail:…'."""
    from rio_cogeo.cogeo import cog_translate, cog_validate
    from rio_cogeo.profiles import cog_profiles

    series_id = pub.series_id
    work = tempfile.mkdtemp(prefix=f"h_{series_id.replace('/', '_')}_")
    try:
        cut, _ = footprint(series_id, work)
        zip_paths = []
        for i, zurl in enumerate(zurls):
            zp = os.path.join(work, f"pub{i}.zip")
            download(encode_url(zurl), zp)
            zip_paths.append(zp)

        plate, shp = prepare_plates(zip_paths, work)
        if not plate:
            print(f"{series_id}: FAIL no plate", file=sys.stderr)
            return "fail:noplate"

        # Free up tmpfs RAM by aggressively deleting the downloaded ZIPs
        for zp in zip_paths:
            try:
                os.remove(zp)
            except Exception:
                pass

        clipped = os.path.join(work, "clipped.tif")
        run(["gdalwarp", "-cutline", cut, "-cutline_srs", "EPSG:4326", "-crop_to_cutline",
             "-t_srs", "EPSG:3857", "-r", "lanczos", "-dstalpha", "-overwrite",
             "-co", "BIGTIFF=YES", plate, clipped])
        cog = os.path.join(work, f"{series_id}.cog.tif")
        prof = dict(cog_profiles.get(COG_COMPRESS))
        prof["bigtiff"] = "IF_SAFER"
        if COG_COMPRESS == "webp":
            prof["quality"] = COG_QUALITY
        elif COG_COMPRESS in ("zstd", "deflate", "lzw"):
            prof["predictor"] = 2

        rgb_clipped = ensure_rgb(clipped)
        cog_translate(rgb_clipped, cog, prof, web_optimized=True, quiet=True)
        # Fallback: some inputs yield an empty/undersized webp COG -> retry lossless LZW,
        # keeping web_optimized so the result is still tiled+overviewed for range reads.
        if not os.path.exists(cog) or os.path.getsize(cog) < 100_000:
            print(f"{series_id}: webp COG empty/undersized -> retry with lzw")
            if os.path.exists(cog):
                os.remove(cog)
            prof["compress"] = "lzw"
            cog_translate(rgb_clipped, cog, prof, web_optimized=True, quiet=True)

        # Free up tmpfs RAM by deleting the intermediate clipped/rgb and plate images
        for f in (clipped, rgb_clipped):
            if f and os.path.exists(f) and f != cog:
                try:
                    os.remove(f)
                except Exception:
                    pass
        if plate and os.path.exists(plate) and plate != cog and not plate.endswith(".vrt"):
            try:
                os.remove(plate)
            except Exception:
                pass

        ok, _, _ = cog_validate(cog)
        if not ok:
            print(f"{series_id}: FAIL cog invalid", file=sys.stderr)
            return "fail:cog"
        # IMMUTABLE: COGs are heavily byte-range-read by the viewer (one request per tile/overview).
        # no-cache means the CDN edge-caches NONE of those → every tile round-trips to GCS origin →
        # the slow "flood of requests" on zoom. COGs are write-once (SKIP_EXISTING skips rewrites),
        # so long-cache is safe; a --force re-harvest needs a CDN cache invalidation.
        gcs.upload(cog, pub.cog_object, content_type=COG_MIME, cache_control=gcs.CACHE_IMMUTABLE)
        if shp:
            import duckdb
            gpq = os.path.join(work, f"{series_id}.units.parquet")
            con = duckdb.connect()
            try:
                con.execute("INSTALL spatial; LOAD spatial;")
                con.execute(f"COPY (SELECT * FROM ST_Read('{shp}')) TO '{gpq}' (FORMAT PARQUET)")
            finally:
                con.close()
            gcs.upload(gpq, f"{identity.UNITS_PREFIX}/{series_id}/{series_id}.units.parquet",
                       content_type=PARQUET_MIME, cache_control=gcs.CACHE_IMMUTABLE)
        if THUMBS:
            th = os.path.join(work, f"{series_id}.thumb.png")
            run(["gdal_translate", "-of", "PNG", "-outsize", "700", "0", cog, th])
            gcs.upload(th, f"{identity.COG_PREFIX}/{series_id}.thumb.png",
                       content_type="image/png", cache_control=gcs.CACHE_IMMUTABLE)
        print(f"{series_id}: OK ({COG_DPI}dpi {COG_COMPRESS} q{COG_QUALITY}) -> {pub.cog_object}")
        return "ok"
    except Exception as e:
        err = (getattr(e, "stderr", "") or str(e)).strip()
        reason = (err.splitlines()[-1] if err.splitlines() else str(e))[:200]
        print(f"{series_id}: FAIL {reason}", file=sys.stderr)
        return f"fail:{type(e).__name__}"
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main() -> int:
    import argparse
    from . import source

    ap = argparse.ArgumentParser(description="Harvest UGS geologic-map publications -> COG")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("series_id", nargs="*", help="Series ID(s) to harvest")
    g.add_argument("--all", action="store_true", help="Harvest all series IDs from the metadata database/CSV")
    ap.add_argument("--limit", type=int, default=None, help="Limit number of publications to harvest")
    ap.add_argument("--dry-run", action="store_true", help="Dry run (check and locate metadata URLs only)")
    ap.add_argument("--force", action="store_true", help="Force harvest even if COG already exists in GCS")
    args = ap.parse_args()

    sids = []
    if args.all:
        print(f"Loading publications from source: {source.source_name()}")
        pubs = source.read_pubs()
        for p in pubs:
            sid = (p.get("series_id") or "").strip()
            if sid:
                sids.append(sid)
        print(f"Found {len(sids)} publications")
    else:
        sids = args.series_id

    # Task sharding: split the worklist across parallel Cloud Run tasks (`--tasks=N`). Each task
    # takes a disjoint stride via CLOUD_RUN_TASK_INDEX/COUNT, so N containers harvest ~1/N each in
    # parallel (dodges the per-task timeout on a big backfill). Default (1 task) = whole list — a
    # NO-OP, so single-pub / steady-state runs are unchanged. `--limit` then applies per shard.
    n = int(os.environ.get("CLOUD_RUN_TASK_COUNT", "1"))
    i = int(os.environ.get("CLOUD_RUN_TASK_INDEX", "0"))
    if n > 1:
        sids = sids[i::n]
        print(f"[harvest] shard {i + 1}/{n}: {len(sids)} publications")

    if args.limit:
        sids = sids[:args.limit]

    rc = 0
    for sid in sids:
        res = harvest_one(sid, dry_run=args.dry_run, force=args.force)
        if res.startswith("fail"):
            rc |= 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
