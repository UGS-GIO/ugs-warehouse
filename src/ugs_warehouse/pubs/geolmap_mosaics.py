"""Build per-scale seamless RASTER mosaics of the published geologic maps as raster PMTiles.

The static-CDN equivalent of the old geolMapPortal ArcGIS Mosaic Datasets (MD_500K / MD_250K /
MD_24K ImageServers): the per-map COGs (geolmap/cogs/<series_id>.cog.tif, from pubs/harvest.py) are
grouped by their publication scale into three tiers and stitched into one raster PMTiles per tier —
geolmap/mosaics/geologic-maps-{tier}.pmtiles. The viewer toggles them like the old portal.

Pipeline per tier (GDAL, COGs read in place over /vsigs):
  gdalbuildvrt (-resolution highest, over /vsigs/<bucket>/...)  ->  `gdal raster tile`
  (multithreaded WebP tiles; GDAL owns adjacent-quad overlap via VRT last-wins + alpha-collar
  fallthrough)  ->  pack the tile tree into MBTiles  ->  `pmtiles convert`  ->  upload

`gdal raster tile` (GDAL 3.11+) replaces the old serial `gdal_translate -of MBTILES` + `gdaladdo`,
which read every COG per tile from one thread and timed out statewide. The base renders with
`-r nearest` when the target zoom is at/above the COGs' native zoom (the tile-grid-aligned masters
copy through pixel-exact); a tier capped BELOW native downsamples with `-r average`. Overviews
always resample. NOTE: the unified `gdal` CLI is provisional (GDAL may rename flags between
releases) — the mosaics image pins a GDAL tag; bump it deliberately and re-check the flags.

Tiles are WebP q90 — the derived display product, so lossy WebP is visually indistinguishable here
(~9x smaller than PNG) and it is NOT the color-authority (that is the lossless master COG). Source-
COG fidelity is whatever the harvest wrote (COG_COMPRESS — deflate-lossless going forward); COGs are
write-once, so pubs harvested before that switch keep their original codec until re-harvested.

By default (`--editions current`) a superseded edition of a quad (per `editions.py`'s edition
graph) is dropped before the VRT — the mosaic shows one current map per quad. `--editions all`
reproduces the old behavior (every COG, every edition stacked). `--quads` scopes a build to named
quads and writes ONLY a scratch `*-test-current`/`*-test-all` pmtiles object (the edition mode is
baked into the suffix) — NO catalog item, so a scoped run can never leak into the live collection
via a later real run's `refresh_catalog()` GCS listing. Inspect the scratch pmtiles directly (e.g.
load it in a MapLibre/pmtiles viewer) via the URL the build prints. A current-only demo and an
all-editions demo of the SAME quads can be built side by side for comparison without either
clobbering the other or the real tier:

    python -m ugs_warehouse.pubs.geolmap_mosaics --scale all
    python -m ugs_warehouse.pubs.geolmap_mosaics --scale 24k --editions all
    python -m ugs_warehouse.pubs.geolmap_mosaics --scale 24k --quads "Park City East Quad" --editions current
    python -m ugs_warehouse.pubs.geolmap_mosaics --scale 24k --quads "Park City East Quad" --editions all
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from ..core import config, gcs, stac
from . import editions, identity, scale, source
from .scale import MOSAIC_TIER_LABEL as TIER_LABEL
from .scale import MOSAIC_TIERS as TIERS

PMTILES_MIME = config.PMTILES_MIME
# Each tier is one STAC item in this collection; the viewer toggles them like the old portal layers.
COLLECTION = "ugs-geologic-maps"
# Statewide extent (the mosaics are clipped to Utah). [W, S, E, N] in EPSG:4326.
UTAH_BBOX = [-114.053, 36.998, -109.041, 42.002]
# Max web-mercator zoom per tier for a STATEWIDE build. `gdal raster tile --max-zoom` enforces this
# directly (the old bake passed -co ZOOM_LEVEL, which the MBTiles driver silently ignored, then
# worked around it by resampling the VRT). The 24k tier is the flagship and its TARGET is z17 (the
# 600 DPI COGs' native detail), BUT a statewide z17 build produces ~tens of GB of tiles, which does
# not fit the RAM-backed /tmp of the current job — so the default stays at a RAM-safe cap until the
# disk-backed / tile-sharded statewide build lands (the ALL-5993 scale follow-up). Run a higher zoom
# explicitly with --maxzoom (and MOSAIC_WORK_DIR on a real disk) once that is in place. A scoped
# --quads build renders at native zoom (full detail on a few maps).
TIER_MAXZOOM = {"24k": 14, "100k": 12, "250k": 12, "500k": 12}
# Lowest zoom to build overviews down to (off the base tiles). A statewide extent is ~1 tile at low
# zoom, so a full pyramid to MOSAIC_MINZOOM is cheap and lets the layer draw when zoomed out.
MOSAIC_MINZOOM = max(0, int(os.environ.get("MOSAIC_MINZOOM", "4")))
# WebP tile quality, clamped to WebP's valid 1-100 (out-of-range crashes the GDAL WebP driver).
TILE_QUALITY = max(1, min(100, int(os.environ.get("MOSAIC_WEBP_QUALITY", "90"))))
# Work dir for the (large) intermediate tile tree + MBTiles. Unset -> the system temp (/tmp, which is
# RAM on Cloud Run — fine for the small tiers, NOT for statewide z17). Point it at a mounted disk for
# the big builds; None lets tempfile use the default.
MOSAIC_WORK_DIR = os.environ.get("MOSAIC_WORK_DIR") or None


def mosaic_object(tier: str, *, suffix: str = "") -> str:
    return f"{identity.MOSAIC_PREFIX}/geologic-maps-{tier}{suffix}.pmtiles"


def _cog_sids() -> set[str]:
    """Series ids that have a harvested COG (geolmap/cogs/<sid>.cog.tif)."""
    pfx = identity.COG_PREFIX.rstrip("/") + "/"
    out: set[str] = set()
    for path in gcs.list_paths(identity.COG_PREFIX, bucket=config.SOURCE_BUCKET):
        name = path[len(pfx):] if path.startswith(pfx) else path
        if name.endswith(".cog.tif"):
            out.add(name[: -len(".cog.tif")].upper())
    return out


def _group_by_tier(edition_mode: str = "current",
                    quads: str | None = None) -> tuple[dict[str, list[str]], dict[str, dict]]:
    """{tier -> [series_id, ...]} for every map that has a COG, by its footprint's portal layer
    (`scale.mosaic_tier_of`), plus {UPPER series_id -> pub record}, so `_write_item` can build member
    links without a second `source.read_pubs()` pass. A COG with no footprint, or in no tiered
    layer, is left out and named on stderr.

    `edition_mode`: "current" (default) drops superseded quad editions before the tier bins fill,
    using the edition graph from `editions.py` — the authoritative mosaic should show one map per
    quad, not every historical revision stacked on top of each other. "all" reproduces the legacy
    behavior (every COG, every edition) and skips the edition graph.

    `quads`: comma-separated quad names (matched casefolded) restricting membership to those
    quads — the scoped/demo build path driven by `--quads` in `main()`.

    The footprints are read once and serve the tiering, the deprecation graph and the `--quads`
    filter."""
    pubs_by_sid = {(p.get("series_id") or "").strip().upper(): p for p in source.read_pubs()}

    rows = editions.footprint_rows()
    qmap = editions.quad_by_series(rows)
    layers = editions.layers_by_series(rows)
    tier_by_sid = editions.mosaic_tier_by_series(layers)

    deprecated_upper: set[str] = set()
    if edition_mode == "current":
        graph = editions.edition_graph(list(pubs_by_sid.values()), quad_by_sid=qmap,
                                       tier_by_sid=tier_by_sid)
        deprecated_upper = {s.upper() for s, e in graph.items() if e["deprecated"]}

    requested_quads: set[str] | None = None
    if quads:
        requested_quads = {q.strip().casefold() for q in quads.split(",") if q.strip()}

    groups: dict[str, list[str]] = {t: [] for t in TIERS}
    untiered: dict[str, list[str]] = {}
    n_deprecated = 0
    n_outside_quads = 0
    for sid in sorted(_cog_sids()):
        if edition_mode == "current" and sid in deprecated_upper:
            n_deprecated += 1
            continue
        if requested_quads is not None:
            quad_name = qmap.get(sid)
            if quad_name is None or quad_name.casefold() not in requested_quads:
                n_outside_quads += 1
                continue
        services, serv_names = layers.get(sid, (frozenset(), frozenset()))
        t = tier_by_sid.get(sid)
        if t is None:
            n_tiers = len(scale.mosaic_tiers(services, serv_names))
            layer_ids = "/".join(sorted(services | serv_names))
            why = ("no footprint" if not services and not serv_names
                   else f"footprints in {n_tiers} tiers: {layer_ids}" if n_tiers > 1
                   else f"no tiered layer: {layer_ids}")
            untiered.setdefault(why, []).append(sid)
            continue
        groups[t].append(sid)
    counts = ", ".join(f"{t}={len(groups[t])}" for t in TIERS)
    detail = f"untiered: {sum(len(v) for v in untiered.values())}"
    if edition_mode == "current":
        detail += f", deprecated editions dropped: {n_deprecated}"
    if requested_quads is not None:
        detail += f", outside --quads: {n_outside_quads}"
    print(f"[mosaics] COGs grouped by portal layer: {counts}  ({detail})")
    for why, sids in sorted(untiered.items()):
        print(f"[mosaics] WARNING: {len(sids)} COG(s) left out ({why}): {', '.join(sids)}", file=sys.stderr)
    return groups, pubs_by_sid


def _vsigs(sid: str) -> str:
    """GDAL /vsigs path to a COG — read in place from the (private) bucket, no download."""
    return f"/vsigs/{config.SOURCE_BUCKET}/{identity.COG_PREFIX}/{sid}.cog.tif"


def _vrt_order(sids: list[str], by_sid: dict[str, dict]) -> list[str]:
    """`sids` ordered ascending by `pub_year` (newest last). `gdalbuildvrt` gives the LAST source
    priority on overlap, so this makes the newest edition draw on top for free — matters for
    `--editions all` (every edition stacked) and the pub_year-tie case. An unparseable/missing year
    sorts to 0 (bottom); ties (including two unparseable years) break on sid for a stable order."""
    def _yr(s: str) -> int:
        y = (by_sid.get(s, {}).get("pub_year") or "").strip()
        return int(y) if len(y) == 4 and y.isdigit() else 0
    return sorted(sids, key=lambda s: (_yr(s), s))


_WEBMERC_Z0_MPP = 156543.03392804097  # web-mercator m/px at zoom 0 (256 px tiles)
_WEBMERC_ORIGIN = 20037508.342789244  # web-mercator half-extent; the tile grid starts at (-o, +o)
# How far (in pixels) the VRT may drift off a zoom's tile grid and still count as on it: origin offset,
# and pixel-size error accumulated across the whole raster. Web-optimized COGs land on it to float
# precision; any real drift makes `nearest` duplicate or drop pixels, which breaks 1-px linework.
ZOOM_MATCH_TOL = 1e-6
# z22 is ~3.7 cm/px. A finer native zoom means a member COG with a bad georeference, not real detail.
MAX_NATIVE_ZOOM = 22


def _grid_offset(v: float, res: float) -> float:
    """How far `v` (metres from the grid origin) is from the nearest pixel edge, in pixels."""
    f = v / res
    return abs(f - round(f))


def _vrt_zoom_and_bounds(vrt: str, env: dict) -> tuple[int, bool, list[float]]:
    """From one `gdalinfo -json` on the VRT: (native web-mercator max zoom, whether the VRT's pixels sit
    on that zoom's tile grid, [W, S, E, N] in EPSG:4326). `gdalbuildvrt -resolution highest` puts the VRT at
    the finest member COG's m/px; the nearest zoom is the mosaic's native max zoom, but only a source
    ON that zoom's pixel grid copies through pixel-exact under `nearest` (a 0.9 m/px COG rounds to z17
    yet has to be downsampled). The WGS84 extent is the mosaic's REAL footprint, so the packed
    MBTiles/PMTiles metadata describes the actual coverage instead of always claiming the whole state."""
    # stdout only: stderr streams to the job log, so a failed /vsigs read shows GDAL's real reason.
    info = json.loads(subprocess.run(["gdalinfo", "-json", "-nofl", vrt], env=env,
                                     stdout=subprocess.PIPE, text=True, check=True).stdout)
    if not isinstance(info, dict):
        raise RuntimeError(f"gdalinfo -json on {vrt} returned {type(info).__name__}, not an object")
    stac_info = info.get("stac")
    epsg = stac_info.get("proj:epsg") if isinstance(stac_info, dict) else None
    if epsg != 3857:
        raise RuntimeError(f"{vrt} reports proj:epsg={epsg!r}, not EPSG:3857; "
                           "the zoom math needs web-mercator metres")
    gt = info.get("geoTransform")
    # type(), not isinstance(): bool is an int subclass. json.loads yields exact int/float.
    if not (isinstance(gt, list) and len(gt) == 6
            and all(type(v) in (int, float) and math.isfinite(v) for v in gt) and gt[1] and gt[5]):
        raise RuntimeError(f"gdalinfo on {vrt} reported no usable geoTransform: {gt!r}")
    size = info.get("size")
    if not (isinstance(size, list) and len(size) == 2 and all(type(v) is int and v > 0 for v in size)):
        raise RuntimeError(f"gdalinfo on {vrt} reported no usable size: {size!r}")
    xres = abs(gt[1])
    zoom = max(0, round(math.log2(_WEBMERC_Z0_MPP / xres)))
    if zoom > MAX_NATIVE_ZOOM:
        raise RuntimeError(f"{vrt}: {xres} m/px implies z{zoom}; check the finest member COG's georeference")
    res = _WEBMERC_Z0_MPP / 2 ** zoom
    # gdalbuildvrt rejects rotated sources; north-up writes an exact 0.
    aligned = (abs(xres - res) * size[0] / res <= ZOOM_MATCH_TOL
               and abs(abs(gt[5]) - res) * size[1] / res <= ZOOM_MATCH_TOL
               and gt[2] == 0 and gt[4] == 0
               and _grid_offset(gt[0] + _WEBMERC_ORIGIN, res) <= ZOOM_MATCH_TOL
               and _grid_offset(_WEBMERC_ORIGIN - gt[3], res) <= ZOOM_MATCH_TOL)
    extent = info.get("wgs84Extent")
    ring = extent.get("coordinates") if isinstance(extent, dict) else None
    if ring and ring[0]:
        lons = [pt[0] for pt in ring[0]]
        lats = [pt[1] for pt in ring[0]]
        bounds = [min(lons), min(lats), max(lons), max(lats)]
    else:
        print(f"[mosaics] WARNING: {vrt} has no usable wgs84Extent; using the statewide bbox",
              file=sys.stderr)
        bounds = list(UTAH_BBOX)
    return zoom, aligned, bounds


def _pack_tiles_to_mbtiles(tile_dir: str, mbtiles: str, *, bounds: list[float], name: str,
                           tile_format: str = "webp") -> tuple[int, int, int]:
    """Pack a `gdal raster tile` XYZ tree ({z}/{x}/{y}.{ext}) into an MBTiles so `pmtiles convert` can
    read it (go-pmtiles takes MBTiles, not a tile directory). Tiles are copied VERBATIM — no re-encode,
    zero added loss. MBTiles rows are TMS (y flipped from the XYZ tree). Returns (n_tiles, minz, maxz)."""
    if not os.path.isdir(tile_dir):
        raise RuntimeError(f"gdal raster tile produced no output dir {tile_dir}")
    zdirs = sorted(int(d) for d in os.listdir(tile_dir)
                   if d.isdigit() and os.path.isdir(os.path.join(tile_dir, d)))
    if not zdirs:
        raise RuntimeError(f"gdal raster tile produced no tiles under {tile_dir}")
    minz, maxz = zdirs[0], zdirs[-1]
    con = sqlite3.connect(mbtiles)
    try:
        # A throwaway temp file: skip the rollback journal and fsyncs; a crash just reruns the bake.
        con.execute("PRAGMA journal_mode = OFF")
        con.execute("PRAGMA synchronous = OFF")
        cur = con.cursor()
        cur.execute("CREATE TABLE metadata (name text NOT NULL UNIQUE, value text)")
        cur.execute("CREATE TABLE tiles (zoom_level int, tile_column int, tile_row int, tile_data blob)")
        cur.execute("CREATE UNIQUE INDEX tile_index ON tiles (zoom_level, tile_column, tile_row)")
        counted = [0]

        def rows():
            # A generator, not a list: a statewide tree is millions of tiles.
            for z in zdirs:
                zdir = os.path.join(tile_dir, str(z))
                for xd in os.listdir(zdir):
                    xpath = os.path.join(zdir, xd)
                    if not (xd.isdigit() and os.path.isdir(xpath)):
                        continue
                    for yf in os.listdir(xpath):
                        ystr, ext = os.path.splitext(yf)
                        if not ystr.isdigit() or not ext:
                            continue                    # skip sidecars (.aux.xml etc.)
                        if ext.lower() != f".{tile_format}":
                            raise RuntimeError(f"unexpected tile {xpath}/{yf}; expected .{tile_format}")
                        with open(os.path.join(xpath, yf), "rb") as fh:
                            blob = fh.read()
                        counted[0] += 1
                        yield z, int(xd), (1 << z) - 1 - int(ystr), blob   # XYZ (top) -> TMS (bottom)

        # Plain INSERT, not OR REPLACE: the unique index makes any duplicate z/x/y fail loud.
        cur.executemany("INSERT INTO tiles VALUES (?,?,?,?)", rows())
        n = counted[0]
        if n == 0:
            raise RuntimeError(f"no tile files under {tile_dir} (zoom dirs {zdirs} were empty)")
        w, s, e, nth = bounds
        cur.executemany("INSERT INTO metadata VALUES (?,?)", (
            ("name", name), ("format", tile_format), ("type", "overlay"),
            ("minzoom", str(minz)), ("maxzoom", str(maxz)),
            ("bounds", f"{w},{s},{e},{nth}"),
            ("center", f"{(w + e) / 2},{(s + nth) / 2},{minz}")))
        con.commit()
    finally:
        con.close()
    return n, minz, maxz


def _band_types(sids: list[str], env: dict) -> dict[str, list[str]]:
    """Each member COG's band data types, from one header read per COG (parallel: I/O bound over
    /vsigs). A failed read raises rather than guessing."""
    def one(sid: str) -> list[str]:
        out = subprocess.run(["gdalinfo", "-json", "-nofl", "-nomd", "-noct", _vsigs(sid)], env=env,
                             stdout=subprocess.PIPE, text=True, check=True).stdout
        return [b["type"] for b in json.loads(out)["bands"]]
    with ThreadPoolExecutor(max_workers=16) as ex:
        return dict(zip(sids, ex.map(one, sids)))


def _byte_members(tier: str, sids: list[str], env: dict) -> list[str]:
    """`sids` minus any COG whose bands aren't all Byte, order kept. gdalbuildvrt keeps only sources
    matching the FIRST source's data type and just warns about the rest, so one 16-bit COG in the
    list would silently drop maps (or, listed first, nearly the whole tier). Excluded COGs are named
    on stderr so the gap is visible."""
    types = _band_types(sids, env)
    bad = {s: t for s, t in types.items() if not t or any(bt != "Byte" for bt in t)}
    if bad:
        detail = ", ".join(f"{s} ({'/'.join(sorted(set(t)))})" for s, t in bad.items())
        print(f"[mosaics] WARNING {tier}: excluding {len(bad)} non-8-bit COG(s) that gdalbuildvrt "
              f"would silently drop or let displace the tier: {detail}", file=sys.stderr)
    return [s for s in sids if s not in bad]


def _check_vrt_sources(vrt: str, expected: list[str]) -> None:
    """Raise if gdalbuildvrt skipped any input. It only warns when it drops a source (type or band
    mismatch, unreadable file), so compare band 1's sources against the input list. Non-8-bit COGs
    are already excluded by `_byte_members`; any other drop is unexpected and stops the tier."""
    band = ET.parse(vrt).getroot().find("VRTRasterBand")   # our own gdalbuildvrt output
    got = {el.text for el in band.iter("SourceFilename")} if band is not None else set()
    missing = [p for p in expected if p not in got]
    if missing:
        raise RuntimeError(f"gdalbuildvrt dropped {len(missing)} of {len(expected)} source(s) from "
                           f"{vrt}: {', '.join(missing[:10])}" + (" ..." if len(missing) > 10 else ""))


def build_tier(tier: str, sids: list[str], by_sid: dict[str, dict], maxz: int | None = None,
                *, suffix: str = "", write_stac_item: bool = True) -> bool:
    """Stitch one tier's COGs into a raster PMTiles and upload it. Returns False if the tier is
    empty. `suffix` (e.g. "-test-current") routes a scoped/--quads demo build to its own object,
    never the real tier — see `mosaic_object`. `write_stac_item=False` (the scoped/--quads path)
    uploads the scratch pmtiles for direct inspection but writes NO STAC item — see `build()`."""
    if not sids:
        print(f"[mosaics] {tier}: no COGs — skipping")
        return False
    # Performance-tuned GDAL environment for reading the COGs in place over /vsigs.
    gdal_env = os.environ.copy()
    gdal_env.update({
        "GDAL_NUM_THREADS": "ALL_CPUS",                  # within-GDAL threading (warp/compress)
        "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",     # no redundant GCS directory scans
        "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.tiff,.vrt",
        "VSI_CACHE": "TRUE",
        "VSI_CACHE_SIZE": "536870912",                   # 512 MB chunk cache for remote files
        "GDAL_HTTP_MAX_RETRY": "10",
        "GDAL_HTTP_RETRY_DELAY": "1",
    })
    gdal_env.setdefault("GDAL_CACHEMAX", "4096")   # MB; the Batch VM sets its own
    sids = _byte_members(tier, sids, gdal_env)
    if not sids:
        print(f"[mosaics] {tier}: no 8-bit COGs left — skipping", file=sys.stderr)
        return False
    with tempfile.TemporaryDirectory(dir=MOSAIC_WORK_DIR) as tmp:
        listfile = os.path.join(tmp, "cogs.txt")
        members = [_vsigs(s) for s in _vrt_order(sids, by_sid)]
        with open(listfile, "w") as fh:
            fh.write("\n".join(members) + "\n")
        vrt = os.path.join(tmp, f"{tier}.vrt")
        tiledir = os.path.join(tmp, f"{tier}-tiles")
        mbtiles = os.path.join(tmp, f"{tier}.mbtiles")
        pmtiles = os.path.join(tmp, f"{tier}.pmtiles")

        # `-resolution highest` keeps the VRT at the finest member COG's m/px, so at the mosaic's
        # native zoom the tile-grid-aligned masters tile 1:1. `-addalpha` gives transparent gaps where
        # no map covers, and is what lets GDAL's VRT drop an upper COG's nodata collar through to the
        # map beneath at a quad seam (last-wins + mask fallthrough — no custom compositing needed).
        print(f"[mosaics] {tier}: VRT over {len(sids)} COGs")
        subprocess.run(["gdalbuildvrt", "-q", "-resolution", "highest", "-addalpha",
                        "-input_file_list", listfile, vrt], env=gdal_env, check=True)
        _check_vrt_sources(vrt, members)

        # `gdal raster tile` (GDAL 3.11+) tiles the VRT multithreaded and builds the overview pyramid in
        # one pass. nearest is a pixel-exact copy only when the source sits on the native zoom's grid
        # and the tier isn't capped below it; anything else is resampled with average.
        native, aligned, bounds = _vrt_zoom_and_bounds(vrt, gdal_env)
        base_maxz = maxz if maxz is not None else native
        base_resampling = "nearest" if aligned and base_maxz >= native else "average"
        minz_arg = min(MOSAIC_MINZOOM, base_maxz)      # never emit --min-zoom > --max-zoom
        zdesc = f"z{minz_arg}-{base_maxz}" + ("" if maxz is not None else " (native)")
        print(f"[mosaics] {tier}: gdal raster tile -> {zdesc}, WebP q{TILE_QUALITY}, "
              f"base={base_resampling} (native z{native})")
        subprocess.run(
            ["gdal", "raster", "tile", "--resampling", base_resampling,
             "--overview-resampling", "average", "-f", "WEBP", "--co", f"QUALITY={TILE_QUALITY}",
             "--skip-blank", "--convention", "xyz",
             "--min-zoom", str(minz_arg), "--max-zoom", str(base_maxz),
             vrt, tiledir], env=gdal_env, check=True)

        # go-pmtiles converts an MBTiles (not a tile tree), so pack the XYZ tree first (verbatim copy).
        # Free each stage as it's consumed: the work dir defaults to the RAM-backed /tmp, so keeping the
        # tile tree + MBTiles + PMTiles all alive at once would triple peak usage for no reason.
        print(f"[mosaics] {tier}: packing tiles -> MBTiles")
        n_tiles, minz, maxz_built = _pack_tiles_to_mbtiles(
            tiledir, mbtiles, bounds=bounds, name=f"geologic-maps-{tier}")
        shutil.rmtree(tiledir)
        print(f"[mosaics] {tier}: {n_tiles} tiles (z{minz}-{maxz_built}) -> PMTiles")
        subprocess.run(["pmtiles", "convert", mbtiles, pmtiles], check=True)
        os.remove(mbtiles)

        obj = mosaic_object(tier, suffix=suffix)
        size_mb = os.path.getsize(pmtiles) // 1024 // 1024
        print(f"[mosaics] {tier}: uploading {size_mb} MB -> {obj}")
        gcs.upload(pmtiles, obj, content_type=PMTILES_MIME, cache_control=gcs.CACHE_MUTABLE)
        if write_stac_item:
            _write_item(tier, sids, obj, by_sid, bounds=bounds)
        else:
            print(f"[mosaics] {tier}: scoped build — no STAC item written (scratch); "
                  f"inspect tiles directly at {config.public_url(obj)}")
        print(f"[mosaics] {tier}: done -> {config.public_url(obj)}")
        return True


def _public_item_href(collection_path: str, item_id: str) -> str:
    """A member map's item in the PUBLIC catalog. Pub items live only there, so a mosaic baked into
    the review catalog still links its members to the public CDN rather than to review paths that
    don't exist."""
    root = config.PUBLIC_CATALOG_URL.rsplit("/", 1)[0]
    return f"{root}/{collection_path}/{item_id}/{item_id}.json"


def _write_item(tier: str, sids: list[str], obj: str, by_sid: dict[str, dict], *,
                bounds: list[float]) -> None:
    """STAC item for one mosaic tier, footprinted by the mosaic's real [W, S, E, N] `bounds`. The
    raster PMTiles is a `visual` pmtiles ASSET (a vector layer would be a web-map LINK instead) —
    that's how the viewer tells a raster mosaic from vector tiles.

    The mosaic is derived by stitching the member COGs, so each member gets a STAC
    `rel:"derived_from"` link — the spec's provenance relation ("a STAC Entity that was used as
    input data in the creation of this Entity") — to its real published item, routed through
    `sink_stac.collection_group` rather than hardcoded to `ugs-publications` (a member can be
    foreign-published `ugs-external` or a Mining District File `ugs-mining-district-files`, and
    hardcoding would 404). A COG with no matching pub record has no item to link to and is skipped,
    but still counts toward `ugs:map_count` (it's physically stitched into the raster). The reverse
    map→mosaic direction is a generic `rel:"related"` on the pub side (sink_stac) — STAC defines no
    reverse of `derived_from`."""
    from . import sink_stac  # function-level: sink_stac never imports geolmap_mosaics, no cycle

    label = TIER_LABEL.get(tier, tier)
    n_maps = len(sids)
    w, s, e, n = bounds
    derived_from: list[dict] = []
    for sid in sids:                      # sids are UPPER (from _cog_sids)
        p = by_sid.get(sid)
        if not p:
            print(f"[mosaics] {tier}: COG {sid} has no pub record — stitched into the raster but not linked (orphan)", file=sys.stderr)
            continue                      # COG present but no pub record -> no STAC item exists to link
        real_sid = (p.get("series_id") or "").strip()
        coll = f"{sink_stac.collection_group(p)}/{sink_stac.series_code(real_sid)}"
        derived_from.append({
            "rel": "derived_from",
            "href": _public_item_href(coll, sink_stac.item_id_for(real_sid)),
            "type": "application/geo+json",
            "title": (p.get("pub_name") or "").strip() or stac.prettify(real_sid)})

    item = stac.build_item(
        item_id=f"geologic-maps-{tier}",
        collection=COLLECTION,
        geometry={"type": "Polygon", "coordinates": [[
            [w, s], [e, s], [e, n], [w, n], [w, s]]]},
        bbox=[w, s, e, n],
        datetime_iso=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        properties={"title": f"Utah Geologic Maps — {label} seamless mosaic",
                    "ugs:scale": tier, "ugs:map_count": n_maps, "ugs:topic": "geologic"},
        assets={"tiles": {"href": config.public_url(obj), "type": PMTILES_MIME,
                          "roles": ["visual"], "ugs:render": "raster",
                          "title": f"Raster PMTiles ({label})"}},
        extra_links=derived_from,
        proj_epsg=4326,
    )
    stac.write_item(item)


def build(scales: list[str], maxz: int | None = None,
          edition_mode: str = "current", quads: str | None = None) -> int:
    """`edition_mode` ("current"|"all") and `quads` (comma-separated quad names) are threaded
    through to `_group_by_tier`. A `--quads` build is a scoped demo/scratch run: it writes ONLY a
    `-test-{edition_mode}`-suffixed scratch pmtiles object (never the real tier), writes NO STAC
    item, and skips the catalog refresh — the suffix carries the edition mode so a current-only
    demo and an all-editions demo of the SAME quads can coexist instead of clobbering each other."""
    groups, by_sid = _group_by_tier(edition_mode=edition_mode, quads=quads)
    suffix = f"-test-{edition_mode}" if quads else ""
    write_stac_item = not bool(quads)
    if quads:
        print(f"[mosaics] SCOPED build (--quads={quads!r}, --editions={edition_mode}) -> "
              f"writing '*{suffix}' items only, real tiers untouched")
    built = 0
    for tier in scales:
        # Statewide (full) builds cap at the tier's TIER_MAXZOOM to keep the tile count sane; a scoped
        # --quads build renders at native zoom (full detail on a few maps). An explicit --maxzoom wins.
        tier_maxz = maxz if maxz is not None else (None if quads else TIER_MAXZOOM.get(tier))
        if build_tier(tier, groups.get(tier, []), by_sid, maxz=tier_maxz,
                      suffix=suffix, write_stac_item=write_stac_item):
            built += 1
    if built and not quads:
        print("[mosaics] refreshing STAC catalog")
        stac.refresh_catalog()
    print(f"[mosaics] complete: {built}/{len(scales)} tiers built")
    return built


def main() -> int:
    ap = argparse.ArgumentParser(description="Build per-scale raster PMTiles mosaics of geologic maps")
    ap.add_argument("--scale", choices=(*TIERS, "all"), action="append",
                    help="Scale tier to build; repeat for several (default all). The statewide 24k tier "
                         "at z17 runs on Cloud Batch (scripts/submit_mosaics_batch.sh).")
    ap.add_argument("--maxzoom", type=int, default=None,
                    help="Cap the base (native) zoom level; default lets GDAL pick from resolution.")
    ap.add_argument("--editions", choices=("current", "all"), default="current",
                    help="current (default) drops superseded quad editions before stitching; "
                         "all reproduces the legacy behavior (every COG, every edition).")
    ap.add_argument("--quads", default=None,
                    help="Comma-separated quad names (as in the footprints quad_name) to restrict "
                         "members to — a scoped demo/scratch build; writes ONLY a scratch "
                         "*-test-current or *-test-all pmtiles (matching --editions), no STAC "
                         "item, and never touches the real tier — inspect via the printed URL.")
    args = ap.parse_args()
    if args.quads is not None and not any(q.strip() for q in args.quads.split(",")):
        ap.error("--quads contained no usable quad names")
    picked = args.scale or ["all"]
    scales = list(TIERS) if "all" in picked else list(dict.fromkeys(picked))
    built = build(scales, maxz=args.maxzoom, edition_mode=args.editions, quads=args.quads)
    # A scoped --quads build under the default --scale all legitimately leaves tiers with no members.
    return 0 if built == len(scales) or (args.quads and built > 0) else 1


if __name__ == "__main__":
    sys.exit(main())
