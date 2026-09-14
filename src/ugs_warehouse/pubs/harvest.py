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

import contextvars
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

from ..core import config, gcs
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
MAX_ZIP_SIZE_MB = int(os.environ.get("MAX_ZIP_SIZE_MB", "0"))


class ZipTooLargeError(Exception):
    """Raised when a publication zip download exceeds the allowed size limit."""
    pass

COG_MIME = config.COG_MIME
PARQUET_MIME = config.PARQUET_MIME

S = requests.Session()
S.headers["User-Agent"] = "ugs-warehouse-pubs"
# The footprint FeatureServer rate-limits under batch load and drops connections; back off.
_retry = Retry(total=6, connect=6, read=6, backoff_factor=1.5,
               status_forcelist=[429, 500, 502, 503, 504], allowed_methods=["GET"])
S.mount("https://", HTTPAdapter(max_retries=_retry))
S.mount("http://", HTTPAdapter(max_retries=_retry))


# --- Per-pub structured logging -------------------------------------------------------------
# Cloud Run's logging agent parses a JSON line on stdout/stderr into `jsonPayload`, so emitting
# {series_id, step, severity, message} lets the admin pull *all logs for one publication* via
# `jsonPayload.series_id="OFR-593"` — a per-pub harvest report instead of a flat firehose. The
# series_id rides a contextvar so helper functions (footprint, corrected_georef, …) tag their lines
# automatically without threading it through every signature.
_series_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("series_id", default="")


def hlog(message: str, *, step: str = "", level: str = "INFO", category: str = "",
         err: bool = False) -> None:
    rec = {"severity": level, "series_id": _series_ctx.get(), "step": step, "message": message}
    if category:
        rec["category"] = category  # ok | expected | attention — triage in the report
    print(json.dumps(rec), file=sys.stderr if err else sys.stdout, flush=True)


# Outcome code → triage category. "expected" = nothing to harvest, legitimately (no action needed);
# "attention" = had data but processing failed (investigate). PDF-only/no-spatial is EXPECTED, not a
# failure — it must not flip the task exit code or burn a retry.
def outcome_category(code: str) -> str:
    if code == "ok":
        return "ok"
    if code.startswith("skip:too_large"):
        return "attention"  # a real map we couldn't process (size cap) — worth a look
    if code.startswith("skip"):
        return "expected"   # placeholder / already-harvested / no spatial data
    return "attention"      # fail:*


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


def download(url, dest, max_bytes: int | None = None):
    with S.get(url, stream=True, timeout=900) as r:
        r.raise_for_status()
        cl = r.headers.get("Content-Length")
        if cl and max_bytes and int(cl) > max_bytes:
            raise ZipTooLargeError(f"size {int(cl)} bytes exceeds limit {max_bytes} bytes")
        written = 0
        with open(dest, "wb") as f:
            for c in r.iter_content(1 << 20):
                written += len(c)
                if max_bytes and written > max_bytes:
                    raise ZipTooLargeError(f"downloaded bytes exceeded limit {max_bytes}")
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


_attachments_cache: dict[str, list[dict]] = {}


def _get_attached_zips(series_id: str) -> tuple[str | None, str | None]:
    """Resolve GeoTIFF and GIS zip URLs from local attachments database (high performance)."""
    global _attachments_cache
    from . import source
    if not _attachments_cache:
        try:
            hlog("pre-loading local attachments database…", step="startup")
            for a in source.read_attachments():
                sid = (a.get("series_id") or "").strip().upper()
                _attachments_cache.setdefault(sid, []).append(a)
            hlog(f"pre-loaded attachments for {len(_attachments_cache)} publications", step="startup")
        except Exception as e:
            hlog(f"WARN failed to load attachments: {e}", step="startup", level="WARNING")
            return None, None

    gt = gis = None
    sid_upper = series_id.strip().upper()
    for a in _attachments_cache.get(sid_upper, []):
        url = (a.get("pub_url") or "").strip()
        if not url.lower().endswith(".zip"):
            continue
        if not url.startswith(("http://", "https://")):
            url = f"https://ugspub.nr.utah.gov/publications/{url}"

        desc = (a.get("extra_data") or "").lower()
        if "geotiff" in desc or "geotiff" in url.lower():
            gt = url
        elif "gis" in desc or "gis" in url.lower() or "plates" in desc:
            gis = url
        else:
            if not gt:
                gt = url
            elif not gis:
                gis = url
    return gt, gis


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


def _sidecar(stem: str, exts: tuple[str, ...]) -> str | None:
    """Sibling of `stem` with one of `exts`, matched case-insensitively.

    The zip extractor accepts any case (`n.lower().endswith(...)`), so an uppercase FOO.TFW lands on
    disk and then an exact-case lookup misses it — the bundle looks unreferenced when it is not.
    """
    directory = os.path.dirname(stem) or "."
    base = os.path.basename(stem).lower()
    try:
        entries = os.listdir(directory)
    except OSError:
        return None
    for entry in entries:
        low = entry.lower()
        if any(low == base + e for e in exts):
            return os.path.join(directory, entry)
    return None


def _prj_srs(work):
    """CRS from any sibling .prj in the bundle — GIS bundles ship .prj for shapefiles but often
    not for the base raster, yet the raster's world-file coords are in that same project CRS."""
    from rasterio.crs import CRS
    found = []
    for root, _dirs, files in os.walk(work):
        found += [os.path.join(root, f) for f in files if f.lower().endswith(".prj")]
    for p in sorted(found):
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


def corrected_georef(gtif, work, zip_path=None, inner_gtif=None):
    """If the GeoTIFF's embedded CRS is unusable, rebuild a VRT whose SRS comes from the .aux.xml
    and whose geotransform comes from the world file. Returns the VRT path, or the original gtif
    when the embedded CRS is fine / no sidecars exist."""
    if not _is_unreferenced(gtif):
        return gtif
    if zip_path and inner_gtif:
        inner_stem = re.sub(r"\.[^.]+$", "", inner_gtif)
        wf = _sidecar(os.path.join(work, inner_stem), (".tfwx", ".tfw", ".wld"))
        aux = _sidecar(os.path.join(work, inner_gtif), (".aux.xml",))
    else:
        stem = re.sub(r"\.[^.]+$", "", gtif)
        wf = _sidecar(stem, (".tfwx", ".tfw", ".wld"))
        aux = _sidecar(gtif, (".aux.xml",))
    srs = _aux_srs(aux) if aux else None
    if not srs:
        srs = _prj_srs(work)
        if srs:
            hlog(f"georef SRS from bundle .prj: {srs}", step="georef")
    if not (wf and srs):
        hlog(f"WARN georef sidecars missing (wf={bool(wf)} srs={bool(srs)}) "
             f"for {os.path.basename(gtif)}", step="georef", level="WARNING")
        return gtif
    A, D, B, E, C, F = [float(x) for x in open(wf).read().split()[:6]]
    gt = (C - 0.5 * A - 0.5 * B, A, B, F - 0.5 * D - 0.5 * E, D, E)
    inner_base = os.path.basename(inner_stem if inner_gtif else stem)
    vrt = os.path.join(work, f"{inner_base}.fixed.vrt")
    run(["gdal_translate", "-q", "-of", "VRT", gtif, vrt])
    xml = open(vrt).read()
    import html
    escaped_srs = html.escape(srs)
    xml = re.sub(r"<SRS[^>]*>.*?</SRS>", f"<SRS>{escaped_srs}</SRS>", xml, flags=re.S)
    gtx = "<GeoTransform>%.12g, %.12g, %.12g, %.12g, %.12g, %.12g</GeoTransform>" % gt
    xml = (re.sub(r"<GeoTransform>.*?</GeoTransform>", gtx, xml, flags=re.S)
           if "<GeoTransform>" in xml else xml.replace("</VRTDataset>", gtx + "</VRTDataset>"))
    open(vrt, "w").write(xml)
    hlog(f"georef rebuilt from sidecars: {srs} + {os.path.basename(wf)}", step="georef")
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
        run(["gdal_translate", "-expand", "rgba", "-co", "COMPRESS=DEFLATE", tif, out])
        return out
    if ci[:3] == (ColorInterp.red, ColorInterp.green, ColorInterp.blue):
        return tif
    if ColorInterp.gray in ci:
        out = tif + ".rgb.tif"
        if n >= 3:
            ci_list = "red,green,blue" + (",alpha" if n >= 4 else "")
            run(["gdal_translate", "-co", "COMPRESS=DEFLATE", "-colorinterp", ci_list, tif, out])
        else:
            args = ["-b", "1", "-b", "1", "-b", "1"] + (["-b", "2"] if n == 2 else [])
            ci_list = "red,green,blue" + (",alpha" if n == 2 else "")
            run(["gdal_translate", "-co", "COMPRESS=DEFLATE"] + args + ["-colorinterp", ci_list, tif, out])
        return out
    return tif


def prepare_plates(zip_paths, work):
    """Extract plates from multiple zips. If COG_DPI>0 and a geospatial PDF is present, rasterize
    it at COG_DPI and georeference from the GeoTIFF; else use the published GeoTIFF. Returns
    (plate_path_or_vrt, shapefile_path_or_None)."""
    import rasterio
    gtif = pdf = shp = None
    target_zip = None
    inner_gtif = None
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
                target_zip = zip_path
                inner_gtif = _gtif
            if _pdf:
                pdf = _pdf
            if _shp:
                shp = _shp
            want = []
            if _pdf:
                want.append(_pdf)
            if _shp:
                stem = re.sub(r"\.shp$", "", _shp, flags=re.I)
                want += [n for n in names if re.sub(r"\.[^.]+$", "", n) == stem]
            if _gtif:
                gstem = re.sub(r"\.[^.]+$", "", _gtif)
                want += [n for n in names if n.startswith(gstem + ".")
                         and n.lower().endswith((".tfwx", ".tfw", ".wld", ".aux.xml", ".prj"))]
            for nm in want:
                z.extract(nm, work)

    shp_path = os.path.join(work, shp) if shp else None
    if not gtif:
        return None, shp_path
    virtual_gtif = f"/vsizip/{target_zip}/{inner_gtif}"
    gtif_path = corrected_georef(virtual_gtif, work, zip_path=target_zip, inner_gtif=inner_gtif)
    if COG_DPI > 0 and pdf:
        prefix = os.path.join(work, "plate")
        dpi = COG_DPI
        while dpi >= 150:
            try:
                run(["pdftoppm", "-png", "-r", str(dpi), os.path.join(work, pdf), prefix])
                break
            except Exception as e:
                hlog(f"pdftoppm failed at {dpi} DPI (likely OOM): {e}. Retrying at lower DPI...",
                     step="plate", level="WARNING")
                dpi = dpi // 2
                # Clean up any partial output
                for filename in os.listdir(work):
                    if filename.startswith("plate-") or filename == "plate.png":
                        try:
                            os.remove(os.path.join(work, filename))
                        except Exception:
                            pass
        png = next((p for p in (prefix + "-1.png", prefix + ".png") if os.path.exists(p)), None)
        with rasterio.open(gtif_path) as g:
            b, crs = g.bounds, g.crs
        if png and crs is not None:
            srs = os.path.join(work, "srs.wkt")
            open(srs, "w").write(crs.to_wkt())
            vrt = os.path.join(work, "plate.vrt")
            run(["gdal_translate", "-q", "-of", "VRT", "-a_srs", srs, "-a_ullr",
                 str(b.left), str(b.top), str(b.right), str(b.bottom), png, vrt])
            return vrt, shp_path
    return gtif_path, shp_path


def harvest_one(series_id: str, dry_run: bool = False, force: bool = False) -> str:
    """Harvest a series_id -> COG (+ units parquet, thumbnail) in GCS. Returns ok|skip|fail:*."""
    pub = identity.Pub.parse(series_id)
    series_id = pub.series_id
    _series_ctx.set(series_id)
    if "XXXX" in series_id:
        hlog("SKIP unpublished placeholder", step="resolve", level="NOTICE", category="expected")
        return "skip:placeholder"
    skip_existing = SKIP_EXISTING and not force and not dry_run
    if skip_existing and gcs.exists(pub.cog_object):
        hlog("SKIP already harvested (COG exists)", step="resolve", level="NOTICE", category="expected")
        return "skip:exists"
    if force and gcs.exists(pub.cog_object):
        hlog("REFUSE overwrite of published COG; publish a revision as a new edition (series_id)",
             step="resolve", level="ERROR", category="unexpected")
        return "fail:write-once"
    gt_url, gis_url = manifest_urls(series_id)
    if not gt_url and not gis_url:
        gt_url, gis_url = _get_attached_zips(series_id)
        if not gt_url and not gis_url:
            try:
                gt_url, gis_url = data_php_urls(series_id)
            except Exception:
                gt_url, gis_url = None, None

    if dry_run:
        hlog(f"dry-run URLs: gt={gt_url}, gis={gis_url}", step="resolve")
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
        # EXPECTED, not a failure: the publication has no spatial bundle to convert (PDF-only). Do
        # not return "fail:*" — that would fail the Cloud Run task and trigger a pointless retry.
        hlog("no spatial data (PDF-only) — nothing to harvest", step="resolve",
             level="NOTICE", category="expected")
        return "skip:nodata"

    status = _harvest_attempt(pub, zurls)
    # FALLBACK: the GIS bundle's base raster sometimes has no usable SRS -> the cutline warp dies.
    # The separate GeoTIFF-Zip is the clean georeferenced plate; retry with it alone.
    if status.startswith("fail") and gt_url and zurls != [gt_url]:
        hlog("GIS source failed → retry GeoTIFF source only", step="source", level="WARNING")
        if _harvest_attempt(pub, [gt_url]) == "ok":
            return "ok"
    return status


def _harvest_attempt(pub: identity.Pub, zurls) -> str:
    """One harvest attempt over a set of source zip URL(s). Returns 'ok' | 'fail:…'."""
    from rio_cogeo.cogeo import cog_translate, cog_validate
    from rio_cogeo.profiles import cog_profiles

    series_id = pub.series_id
    _series_ctx.set(series_id)
    work = tempfile.mkdtemp(prefix=f"h_{series_id.replace('/', '_')}_")
    try:
        cut, n_feat = footprint(series_id, work)
        zip_paths = []
        max_b = MAX_ZIP_SIZE_MB * 1024 * 1024 if MAX_ZIP_SIZE_MB > 0 else None
        hlog(f"downloading {len(zurls)} source zip(s)", step="download")
        for i, zurl in enumerate(zurls):
            zp = os.path.join(work, f"pub{i}.zip")
            download(encode_url(zurl), zp, max_bytes=max_b)
            zip_paths.append(zp)

        plate, shp = prepare_plates(zip_paths, work)
        if not plate:
            hlog("FAIL no plate (had a bundle but no usable raster)", step="plate",
                 level="ERROR", category="attention", err=True)
            return "fail:noplate"

        clipped = os.path.join(work, "clipped.tif")
        # No footprint in the index → empty cutline → gdalwarp "cannot compute bounds of cutline".
        # Fall back to an uncropped warp (full sheet) instead of failing the pub.
        if n_feat > 0:
            cutline_args = ["-cutline", cut, "-cutline_srs", "EPSG:4326", "-crop_to_cutline"]
            hlog("warping (cutline + reproject 3857) → COG", step="cog")
        else:
            cutline_args = []
            hlog("no map footprint in the index — warping uncropped (full sheet)",
                 step="cog", level="WARNING")
        run(["gdalwarp", *cutline_args,
             "-t_srs", "EPSG:3857", "-r", "lanczos", "-dstalpha", "-overwrite",
             "-co", "BIGTIFF=YES", "-co", "COMPRESS=DEFLATE", plate, clipped])
        cog = os.path.join(work, f"{series_id}.cog.tif")
        prof = dict(cog_profiles.get(COG_COMPRESS))
        prof["bigtiff"] = "IF_SAFER"
        if COG_COMPRESS == "webp":
            prof["quality"] = COG_QUALITY
        elif COG_COMPRESS in ("zstd", "deflate", "lzw"):
            prof["predictor"] = 2

        rgb_clipped = ensure_rgb(clipped)

        def _to_lzw(why: str) -> None:
            hlog(f"webp COG {why} → retry with lzw", step="cog", level="WARNING")
            if os.path.exists(cog):
                os.remove(cog)
            prof["compress"] = "lzw"
            prof.pop("quality", None)          # webp-only; lzw rejects it
            cog_translate(rgb_clipped, cog, prof, web_optimized=True, quiet=True)

        # webp is 8-bit-only and raises on 16-bit/float plates; lossless lzw keeps web_optimized
        # so the result is still tiled + overviewed for range reads.
        try:
            cog_translate(rgb_clipped, cog, prof, web_optimized=True, quiet=True)
        except Exception as e:  # noqa: BLE001 — any encode failure is worth one lossless retry
            if COG_COMPRESS != "webp":
                raise
            _to_lzw(f"failed ({type(e).__name__}: {str(e)[:80]})")
        else:
            if not os.path.exists(cog) or os.path.getsize(cog) < 100_000:
                _to_lzw("empty/undersized")

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

        # Free up tmpfs RAM by deleting the downloaded ZIPs
        for zp in zip_paths:
            try:
                os.remove(zp)
            except Exception:
                pass

        ok, _, _ = cog_validate(cog)
        if not ok:
            hlog("FAIL cog invalid", step="validate", level="ERROR", category="attention", err=True)
            return "fail:cog"
        # IMMUTABLE: COGs are heavily byte-range-read by the viewer (one request per tile/overview).
        # no-cache means the CDN edge-caches NONE of those → every tile round-trips to GCS origin →
        # the slow "flood of requests" on zoom. COGs are write-once (upload_write_once refuses to
        # overwrite an existing object) — a revision publishes as a new edition (new series_id),
        # never an in-place rewrite, so long-cache is always safe.
        gcs.upload_write_once(cog, pub.cog_object, content_type=COG_MIME, cache_control=gcs.CACHE_IMMUTABLE)
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
        used = prof.get("compress", COG_COMPRESS)          # may have fallen back from webp
        qual = f" q{COG_QUALITY}" if used == "webp" else ""
        hlog(f"OK ({COG_DPI}dpi {used}{qual}) → {pub.cog_object}", step="result", category="ok")
        return "ok"
    except ZipTooLargeError as e:
        hlog(f"zip too large to process (size cap): {e}", step="download",
             level="WARNING", category="attention")
        return "skip:too_large"
    except Exception as e:
        err = (getattr(e, "stderr", "") or str(e)).strip()
        reason = (err.splitlines()[-1] if err.splitlines() else str(e))[:200]
        hlog(f"FAIL {reason}", step="result", level="ERROR", category="attention", err=True)
        return f"fail:{type(e).__name__}"
    finally:
        shutil.rmtree(work, ignore_errors=True)


def exit_code(tally: dict[str, int], *, strict: bool = False) -> int:
    """0 for per-publication failures; they are data, not a broken run.

    ~86 publications are permanently unharvestable (their bundle carries no raster), so failing on
    any per-pub failure meant the job exited 1 on EVERY run — the job-failure alert then fires every
    time and a real breakage is indistinguishable from known-bad inputs.

    Deliberately no "everything attempted failed" heuristic: a shard can legitimately draw only
    unharvestable publications, so that would false-alarm. Infrastructure failures still exit
    non-zero because they raise. Per-pub failures surface as category="attention" (ops console) —
    watch that count, not this exit code. `--strict` restores the old all-or-nothing behaviour.
    """
    return 1 if (strict and tally["attention"]) else 0


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
    ap.add_argument("--strict", action="store_true",
                    help="exit non-zero if ANY publication needs attention (default: only when "
                         "everything attempted failed)")
    args = ap.parse_args()

    sids = []
    if args.all:
        hlog(f"loading publications from source: {source.source_name()}", step="startup")
        pubs = source.read_pubs()
        for p in pubs:
            sid = (p.get("series_id") or "").strip()
            if sid:
                sids.append(sid)
        hlog(f"found {len(sids)} publications", step="startup")
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
        hlog(f"shard {i + 1}/{n}: {len(sids)} publications", step="shard")

    if args.limit:
        sids = sids[:args.limit]

    tally = {"ok": 0, "expected": 0, "attention": 0}
    for sid in sids:
        res = harvest_one(sid, dry_run=args.dry_run, force=args.force)
        tally[outcome_category(res)] += 1
    _series_ctx.set("")  # the summary is job-level, not scoped to the last pub
    hlog(f"run complete: {tally['ok']} ok, {tally['expected']} expected (skip / PDF-only), "
         f"{tally['attention']} need attention", step="summary",
         level="WARNING" if tally["attention"] else "NOTICE")
    return exit_code(tally, strict=args.strict)


if __name__ == "__main__":
    sys.exit(main())
