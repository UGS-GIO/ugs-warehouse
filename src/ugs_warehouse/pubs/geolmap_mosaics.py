"""Build per-scale seamless RASTER mosaics of the published geologic maps as raster PMTiles + COG.

The static-CDN equivalent of the old geolMapPortal ArcGIS Mosaic Datasets (MD_500K / MD_250K /
MD_24K ImageServers): the per-map COGs (geolmap/cogs/<series_id>.cog.tif, from pubs/harvest.py) are
grouped by their publication scale into three tiers and stitched into one raster PMTiles per tier —
geolmap/mosaics/geologic-maps-{tier}.pmtiles — and one Web Mercator WebP COG beside it,
geologic-maps-{tier}.cog.tif, which the ImageServer serves. The viewer toggles them like the old portal.

Pipeline per tier (GDAL, COGs read in place over /vsigs):
  gdalbuildvrt (-resolution highest, over /vsigs/<bucket>/...)  ->  `gdal raster tile`
  (multithreaded WebP tiles; GDAL owns adjacent-quad overlap via VRT last-wins + alpha-collar
  fallthrough)  ->  write the tile tree into PMTiles  ->  upload
  ->  lossless master GeoTIFF on the COG zoom's tile grid  ->  `gdal_translate -of COG`
  (GoogleMapsCompatible, WebP, internal overviews)  ->  upload

A rebake is incremental by default: the tier Item records each member's COG etag and footprint
(`ugs:members`) and the build settings (`ugs:mosaic_build`). When only some members were added,
rewritten or removed, only the tiles under their old and new footprints are rendered again and the
rest are copied from the published PMTiles; the stored master is patched under the same footprints
and re-encoded to the COG, so unchanged areas encode to the same bytes. No record, other settings,
more than MOSAIC_INCREMENTAL_MAX_FRACTION of the members changed, or `--full` runs the full build.

A tier capped below its native zoom is tiled from a second VRT built ON the cap zoom's grid
(`-tr -tap -r average`): the sources downsample through their own overviews, ~3x faster than warping
the native VRT, and its pixels then copy through `nearest`. The COG is built the same way at
min(MOSAIC_COG_MAXZOOM, the PMTiles max zoom); warping the native z17 VRT to a COG was too slow.

`gdal raster tile` (GDAL 3.11+) replaces the old serial `gdal_translate -of MBTILES` + `gdaladdo`,
which read every COG per tile from one thread and timed out statewide. The base renders with
`-r nearest` when the target zoom is at/above the COGs' native zoom (the tile-grid-aligned masters
copy through pixel-exact); a tier capped BELOW native tiles its grid VRT (above). Overviews
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
import subprocess
import sys
import tempfile
import urllib.parse
import xml.etree.ElementTree as ET
from array import array
from collections.abc import Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import NamedTuple

from pmtiles.convert import mbtiles_to_header_json
from pmtiles.reader import MmapSource, Reader, all_tiles
from pmtiles.tile import tileid_to_zxy, zxy_to_tileid
from pmtiles.writer import Writer

from ..core import cdn, config, gcs, stac
from . import editions, identity, scale, source
from .scale import MOSAIC_TIER_LABEL as TIER_LABEL
from .scale import MOSAIC_TIERS as TIERS

GEOJSON = "application/geo+json"

PMTILES_MIME = config.PMTILES_MIME
# Each tier is one STAC item in this collection; the viewer toggles them like the old portal layers.
COLLECTION = "ugs-geologic-maps"
# Statewide extent (the mosaics are clipped to Utah). [W, S, E, N] in EPSG:4326.
UTAH_BBOX = [-114.053, 36.998, -109.041, 42.002]
# Max zoom per tier for a STATEWIDE build. 24k and 100k bake on Cloud Batch, where
# submit_mosaics_batch.sh passes z17 and z15; a --quads build without --maxzoom renders native.
TIER_MAXZOOM = {"24k": 14, "100k": 15, "250k": 12, "500k": 12}
# Lowest zoom to build overviews down to (off the base tiles). A statewide extent is ~1 tile at low
# zoom, so a full pyramid to MOSAIC_MINZOOM is cheap and lets the layer draw when zoomed out.
MOSAIC_MINZOOM = max(0, int(os.environ.get("MOSAIC_MINZOOM", "4")))
# WebP tile quality, clamped to WebP's valid 1-100 (out-of-range crashes the GDAL WebP driver).
TILE_QUALITY = max(1, min(100, int(os.environ.get("MOSAIC_WEBP_QUALITY", "90"))))
# The COG's max zoom: z15 kept a 23-map 100k benchmark to 709 s and 1.33 GB.
MOSAIC_COG_MAXZOOM = int(os.environ.get("MOSAIC_COG_MAXZOOM", "15"))
# Work dir for the (large) intermediate tile tree + PMTiles. Unset -> the system temp (/tmp, which is
# RAM on Cloud Run — fine for the small tiers, NOT for statewide z17). Point it at a mounted disk for
# the big builds; None lets tempfile use the default.
MOSAIC_WORK_DIR = os.environ.get("MOSAIC_WORK_DIR") or None
# Above this share of changed members a rebake is full: the dirty area would be most of the tier.
INCREMENTAL_MAX_FRACTION = float(os.environ.get("MOSAIC_INCREMENTAL_MAX_FRACTION", "0.3"))
MEMBERS_PROP = "ugs:members"
BUILD_PROP = "ugs:mosaic_build"

Rect = tuple[int, int, int, int]   # inclusive XYZ tile range: min x, min y, max x, max y


def mosaic_object(tier: str, *, suffix: str = "", ext: str = "pmtiles") -> str:
    return f"{identity.MOSAIC_PREFIX}/geologic-maps-{tier}{suffix}.{ext}"


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
    """`sids` with reissues first (see `_is_reissue`), then by `pub_year`, missing years lowest and ties
    by sid. `gdalbuildvrt` draws the LAST source on top, so the newest original map wins an overlap."""
    def _yr(s: str) -> int:
        y = (by_sid.get(s, {}).get("pub_year") or "").strip()
        return int(y) if len(y) == 4 and y.isdigit() else 0
    return sorted(sids, key=lambda s: (not _is_reissue(by_sid.get(s, {})), _yr(s), s))


def _is_reissue(pub: dict) -> bool:
    """A GIS reproduction of an older map. Its pub_year is the reissue's, not the mapping's, so it
    draws beneath every original map and shows only where no other map covers the ground."""
    return "gis reproduction" in (pub.get("pub_name") or "").casefold()


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
    PMTiles metadata describes the actual coverage instead of always claiming the whole state."""
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


def _write_pmtiles(tile_dir: str, out: str, *, bounds: list[float], name: str) -> tuple[int, int, int]:
    """Write a `gdal raster tile` XYZ tree ({z}/{x}/{y}.webp) into a PMTiles archive. Tiles are copied
    verbatim, in tile-id order so the archive is clustered. Returns (n_tiles, minz, maxz).

    Not `pmtiles.convert.disk_to_pmtiles`: it reads a `6.webp.aux.xml` sidecar as tile 6 and writes the
    XML over the real tile, and it keeps every tile file until the end, so the tree, the writer's
    tempfile and the archive are all on disk at once. Here each tile file is deleted once copied."""
    if not os.path.isdir(tile_dir):
        raise RuntimeError(f"gdal raster tile produced no output dir {tile_dir}")
    tiles = _tile_ids(tile_dir)
    if not tiles:
        raise RuntimeError(f"no tile files under {tile_dir}")
    return _pack(out, ((t, _take_tile(tile_dir, t)) for t in tiles), bounds=bounds, name=name)


def _tile_ids(tile_dir: str) -> array:
    """The sorted tile ids of an XYZ tree, 8 bytes each: a statewide z17 tree is millions of tiles.
    The path is rebuilt from the id when the tile is copied."""
    tiles = array("Q")
    if not os.path.isdir(tile_dir):
        return tiles
    for zd in os.listdir(tile_dir):
        if not (zd.isdigit() and os.path.isdir(zpath := os.path.join(tile_dir, zd))):
            continue
        for xd in os.listdir(zpath):
            if not (xd.isdigit() and os.path.isdir(xpath := os.path.join(zpath, xd))):
                continue
            for yf in os.listdir(xpath):
                ystr, ext = os.path.splitext(yf)
                if not ystr.isdigit() or not ext:
                    continue                    # skip sidecars (.aux.xml etc.)
                if ext.lower() != ".webp":
                    raise RuntimeError(f"unexpected tile {xpath}/{yf}; expected .webp")
                tiles.append(zxy_to_tileid(int(zd), int(xd), int(ystr)))
    return array("Q", sorted(tiles))


def _take_tile(tile_dir: str, tid: int) -> bytes:
    """A tile's bytes from the XYZ tree; the file is deleted once read."""
    path = os.path.join(tile_dir, "{}/{}/{}.webp".format(*tileid_to_zxy(tid)))
    with open(path, "rb") as fh:
        data = fh.read()
    os.remove(path)
    return data


def _pack(out: str, tiles: Iterable[tuple[int, bytes]], *, bounds: list[float],
          name: str) -> tuple[int, int, int]:
    """Write (tile id, bytes) pairs, in ascending id order, into a PMTiles archive."""
    n, minz, maxz = 0, None, 0
    with open(out, "wb") as f:
        writer = Writer(f)
        for tid, data in tiles:
            writer.write_tile(tid, data)
            z = tileid_to_zxy(tid)[0]
            minz = z if minz is None else min(minz, z)
            maxz = max(maxz, z)
            n += 1
        if not n:
            raise RuntimeError(f"no tiles to write into {out}")
        # The library's own MBTiles-metadata mapper; with no `center` it opens at the coarsest zoom.
        header, metadata = mbtiles_to_header_json({
            "name": name, "type": "overlay", "format": "webp", "minzoom": minz, "maxzoom": maxz,
            "bounds": ",".join(str(v) for v in bounds)})
        writer.finalize(header, metadata)
    return n, minz, maxz


def _merged_tiles(old: str, tile_dir: str, dirty: dict[int, list[Rect]]) -> Iterator[tuple[int, bytes]]:
    """The published archive `old` with every tile under `dirty` replaced by the tree's tile at that
    id, or dropped when the tree has none (that area is blank now). Other tiles are copied as
    stored. Both inputs are walked in tile-id order, so the result streams out clustered."""
    new = iter(_tile_ids(tile_dir))
    nxt = next(new, None)
    with open(old, "rb") as f:
        for zxy, data in all_tiles(MmapSource(f)):
            tid = zxy_to_tileid(*zxy)
            while nxt is not None and nxt < tid:
                yield nxt, _take_tile(tile_dir, nxt)
                nxt = next(new, None)
            if not _in_rects(dirty.get(zxy[0], ()), zxy[1], zxy[2]):
                yield tid, data
    while nxt is not None:
        yield nxt, _take_tile(tile_dir, nxt)
        nxt = next(new, None)


def _tile_range(bbox: list[float], z: int) -> Rect:
    """The XYZ tiles at zoom `z` that a [W, S, E, N] box touches. An edge on a tile boundary does not
    pull in the neighbor tile (float noise in the footprint is absorbed by `eps`)."""
    n = 2 ** z
    eps = 1e-7

    def ty(lat: float) -> float:
        lat = max(-85.0511287798, min(85.0511287798, lat))
        return (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n

    def clamp(v: int) -> int:
        return max(0, min(n - 1, v))

    w, s, e, nn = bbox
    return (clamp(math.floor((w + 180) / 360 * n + eps)), clamp(math.floor(ty(nn) + eps)),
            clamp(math.ceil((e + 180) / 360 * n - eps) - 1), clamp(math.ceil(ty(s) - eps) - 1))


def _merge_rects(rects: Iterable[Rect]) -> list[Rect]:
    """Touching or overlapping ranges joined into their bounding range, so each is rendered once."""
    out: list[Rect] = []
    for r in rects:
        while True:
            hit = next((o for o in out if r[0] <= o[2] + 1 and o[0] <= r[2] + 1
                        and r[1] <= o[3] + 1 and o[1] <= r[3] + 1), None)
            if hit is None:
                break
            out.remove(hit)
            r = (min(r[0], hit[0]), min(r[1], hit[1]), max(r[2], hit[2]), max(r[3], hit[3]))
        out.append(r)
    return sorted(out)


def dirty_rects(boxes: list[list[float]], minz: int, maxz: int) -> dict[int, list[Rect]]:
    """{zoom: tile ranges} under `boxes` at every zoom from `minz` to `maxz`. A range at a lower zoom
    is the parents of the one above it, so every overview over a change is in it."""
    return {z: _merge_rects(_tile_range(b, z) for b in boxes) for z in range(minz, maxz + 1)}


def _in_rects(rects: Iterable[Rect], x: int, y: int) -> bool:
    return any(r[0] <= x <= r[2] and r[1] <= y <= r[3] for r in rects)


def _rect_metres(rect: Rect, z: int) -> tuple[float, float, float, float]:
    """A tile range's web-mercator extent: (min x, min y, max x, max y)."""
    size = _WEBMERC_Z0_MPP / 2 ** z * 256
    return (-_WEBMERC_ORIGIN + rect[0] * size, _WEBMERC_ORIGIN - (rect[3] + 1) * size,
            -_WEBMERC_ORIGIN + (rect[2] + 1) * size, _WEBMERC_ORIGIN - rect[1] * size)


def changed_members(prev: dict | None, prev_build: dict | None, etags: dict[str, str],
                    build: dict) -> list[str] | None:
    """The members whose COG was added, rewritten or removed since the published bake, by etag.
    None when the tier needs a full build: no record, other build settings, or more than
    INCREMENTAL_MAX_FRACTION of the members changed."""
    if not prev or prev_build != build:
        return None
    changed = sorted(s for s in prev.keys() | etags.keys()
                     if s not in prev or s not in etags or prev[s].get("etag") != etags[s])
    if len(changed) > INCREMENTAL_MAX_FRACTION * max(len(prev), len(etags)):
        return None
    return changed


def _band_types(sids: list[str], env: dict) -> dict[str, list[str]]:
    """Each member COG's band data types, from one header read per COG (parallel: I/O bound over
    /vsigs). A failed read raises rather than guessing."""
    def one(sid: str) -> list[str]:
        out = subprocess.run(["gdalinfo", "-json", "-nofl", "-nomd", "-noct", _vsigs(sid)], env=env,
                             stdout=subprocess.PIPE, text=True, check=True).stdout
        return [b["type"] for b in json.loads(out)["bands"]]
    with ThreadPoolExecutor(max_workers=16) as ex:
        return dict(zip(sids, ex.map(one, sids)))


def _cog_bboxes(sids: list[str], env: dict) -> dict[str, list[float] | None]:
    """Each member COG's [W, S, E, N] footprint from its header; None for a COG that reports no
    WGS84 extent. Not rounded: an edge on a tile boundary must stay on it (`_tile_range`)."""
    def one(sid: str) -> list[float] | None:
        out = subprocess.run(["gdalinfo", "-json", "-nofl", "-nomd", "-noct", _vsigs(sid)], env=env,
                             stdout=subprocess.PIPE, text=True, check=True).stdout
        ring = (json.loads(out).get("wgs84Extent") or {}).get("coordinates") or [[]]
        if not ring[0]:
            return None
        lons, lats = [p[0] for p in ring[0]], [p[1] for p in ring[0]]
        return [min(lons), min(lats), max(lons), max(lats)]
    with ThreadPoolExecutor(max_workers=16) as ex:
        return dict(zip(sids, ex.map(one, sids)))


def _byte_members(tier: str, sids: list[str], env: dict,
                  types: dict[str, list[str]] | None = None) -> list[str]:
    """`sids` minus any COG whose bands aren't all Byte, order kept. gdalbuildvrt keeps only sources
    matching the FIRST source's data type and just warns about the rest, so one 16-bit COG in the
    list would silently drop maps (or, listed first, nearly the whole tier). Excluded COGs are named
    on stderr so the gap is visible. `types` are the COGs' band types when the caller already has
    them (the identify layers cache them); otherwise each header is read."""
    types = _band_types(sids, env) if types is None else types
    bad = {s: t for s, t in types.items() if not t or any(bt != "Byte" for bt in t)}
    if bad:
        detail = ", ".join(f"{s} ({'/'.join(sorted(set(t)))})" for s, t in bad.items())
        print(f"[mosaics] WARNING {tier}: excluding {len(bad)} non-8-bit COG(s) that gdalbuildvrt "
              f"would silently drop or let displace the tier: {detail}", file=sys.stderr)
    return [s for s in sids if s not in bad]


def tier_members(tier: str, sids: list[str], by_sid: dict[str, dict], env: dict,
                 types: dict[str, list[str]] | None = None) -> list[str]:
    """The maps a tier's mosaic is stitched from, in draw order: `sids` minus the non-8-bit COGs
    (`_byte_members`), then `_vrt_order`. The bake and the identify layers both take their members
    from here."""
    return _vrt_order(_byte_members(tier, sids, env, types), by_sid)


def gdal_env() -> dict:
    """The GDAL environment for reading the COGs in place over /vsigs, tuned for speed."""
    env = os.environ.copy()
    env.update({
        "GDAL_NUM_THREADS": "ALL_CPUS",                  # within-GDAL threading (warp/compress)
        "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",     # no redundant GCS directory scans
        "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.tiff,.vrt",
        "VSI_CACHE": "TRUE",
        "VSI_CACHE_SIZE": "536870912",                   # 512 MB chunk cache for remote files
        "GDAL_HTTP_MAX_RETRY": "10",
        "GDAL_HTTP_RETRY_DELAY": "1",
    })
    env.setdefault("GDAL_CACHEMAX", "4096")   # MB; the Batch VM sets its own
    return env


def _build_vrt(listfile: str, vrt: str, members: list[str], env: dict, zoom: int | None = None) -> str:
    """The tier VRT, at the finest member's m/px or on `zoom`'s tile grid (sources averaged down).
    `-addalpha` gives transparent gaps where no map covers, and is what lets GDAL's VRT drop an upper
    COG's nodata collar through to the map beneath at a quad seam (last-wins + mask fallthrough)."""
    res = ["-resolution", "highest"]
    if zoom is not None:
        r = str(_WEBMERC_Z0_MPP / 2 ** zoom)
        res = ["-tr", r, r, "-tap", "-r", "average"]
    subprocess.run(["gdalbuildvrt", "-q", *res, "-addalpha", "-input_file_list", listfile, vrt],
                   env=env, check=True)
    _check_vrt_sources(vrt, members)
    return vrt


def _projwin(rect: Rect, z: int) -> list[str]:
    minx, miny, maxx, maxy = _rect_metres(rect, z)
    return ["-projwin", *(repr(v) for v in (minx, maxy, maxx, miny))]


def _write_master(vrt: str, out: str, rect: Rect, zoom: int, env: dict) -> None:
    """The lossless master GeoTIFF of `rect` from a VRT on `zoom`'s grid: tiled on the tile grid, so a
    patch writes whole blocks, and sparse, so ground with no map costs nothing."""
    subprocess.run(
        ["gdal_translate", "-q", *_projwin(rect, zoom), vrt, out, "-of", "GTiff",
         "-co", "TILED=YES", "-co", "BLOCKXSIZE=256", "-co", "BLOCKYSIZE=256",
         "-co", "COMPRESS=ZSTD", "-co", "PREDICTOR=2", "-co", "SPARSE_OK=TRUE",
         "-co", "BIGTIFF=YES", "-co", "NUM_THREADS=ALL_CPUS"], env=env, check=True)


def _master_rect(path: str, zoom: int) -> Rect:
    """The tile range a master GeoTIFF covers."""
    import rasterio

    size = _WEBMERC_Z0_MPP / 2 ** zoom * 256
    with rasterio.open(path) as ds:
        left, bottom, right, top = ds.bounds
    return (round((left + _WEBMERC_ORIGIN) / size), round((_WEBMERC_ORIGIN - top) / size),
            round((right + _WEBMERC_ORIGIN) / size) - 1, round((_WEBMERC_ORIGIN - bottom) / size) - 1)


def _patch_master(vrt: str, master: str, rects: list[Rect], zoom: int, env: dict, tmp: str) -> None:
    """Overwrite each tile range of the master with the VRT's pixels, one block row at a time. Blank
    ground in the VRT (a removed map) overwrites too."""
    import rasterio
    from rasterio.windows import Window, from_bounds

    patch = os.path.join(tmp, "patch.tif")
    with rasterio.open(master, "r+") as dst:
        for rect in rects:
            subprocess.run(["gdal_translate", "-q", *_projwin(rect, zoom), "-co", "TILED=YES",
                            vrt, patch], env=env, check=True)
            win = from_bounds(*_rect_metres(rect, zoom), dst.transform).round_offsets().round_lengths()
            with rasterio.open(patch) as src:
                if (src.width, src.height) != (win.width, win.height):
                    raise RuntimeError(f"patch {src.width}x{src.height} does not fill {win}")
                for row in range(0, src.height, 256):
                    h = min(256, src.height - row)
                    dst.write(src.read(window=Window(0, row, src.width, h)),
                              window=Window(win.col_off, win.row_off + row, win.width, h))
            os.remove(patch)


def _write_cog(src: str, out: str, zoom: int, env: dict, rect: Rect | None = None) -> None:
    """A Web Mercator WebP COG with internal overviews, from a raster on `zoom`'s grid (the tile
    range `rect` of it, when given)."""
    subprocess.run(
        ["gdal_translate", "-q", *(_projwin(rect, zoom) if rect else []), src, out,
         "-of", "COG", "-co", "TILING_SCHEME=GoogleMapsCompatible",
         "-co", f"ZOOM_LEVEL={zoom}", "-co", "COMPRESS=WEBP", "-co", f"QUALITY={TILE_QUALITY}",
         "-co", "WARP_RESAMPLING=nearest", "-co", "OVERVIEW_RESAMPLING=average",
         "-co", "BIGTIFF=YES",
         "-co", "NUM_THREADS=ALL_CPUS"], env=env, check=True)


def _cog_asset(path: str, obj: str, meta: gcs.FileMeta, label: str) -> dict:
    """The COG's STAC asset, projection and bands from its header (rio-stac). The ImageServer lists
    an item by a GeoTIFF asset keyed `visual`, `cog` or `data`; `_write_item` keys it `visual`."""
    import rasterio

    from .sink_stac import cog_asset_fields

    with rasterio.open(path) as ds:
        fields = cog_asset_fields(ds)
    return {"href": config.public_url(obj), "type": config.COG_MIME, "roles": ["visual"],
            "title": f"Cloud-Optimized GeoTIFF ({label}, Web Mercator)", **fields,
            **stac.file_fields(meta)}


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


def _tile_argv(src: str, out: str, resampling: str, minz: int, maxz: int, *extra: str) -> list[str]:
    return ["gdal", "raster", "tile", "--resampling", resampling,
            "--overview-resampling", "average", "-f", "WEBP", "--co", f"QUALITY={TILE_QUALITY}",
            "--skip-blank", "--convention", "xyz", "--webviewer", "none",
            "--min-zoom", str(minz), "--max-zoom", str(maxz), *extra, src, out]


def _range_args(rect: Rect) -> list[str]:
    return ["--min-x", str(rect[0]), "--max-x", str(rect[2]), "--min-y", str(rect[1]),
            "--max-y", str(rect[3]), "--no-intersection-ok"]


def _render_dirty(dirty: dict[int, list[Rect]], tiledir: str, base_vrt: str, base_resampling: str,
                  maxz: int, old: str, env: dict) -> None:
    """Render the tiles under `dirty` into `tiledir`: the base zoom from the VRT as the full build
    does, then each overview zoom from its children, as GDAL's pyramid does.

    GDAL builds a parent only from children inside the run's range, so each overview range runs with
    all of its children in place: the changed ones already in `tiledir`, the rest copied from the
    published archive `old` for the run. `--resume` keeps GDAL from rendering those again."""
    for rect in dirty[maxz]:
        subprocess.run(_tile_argv(base_vrt, tiledir, base_resampling, maxz, maxz, *_range_args(rect)),
                       env=env, check=True)
    with open(old, "rb") as f:
        reader = Reader(MmapSource(f))
        for z in range(maxz - 1, min(dirty) - 1, -1):
            for x0, y0, x1, y1 in dirty[z]:
                children = (2 * x0, 2 * y0, 2 * x1 + 1, 2 * y1 + 1)
                copied = []
                for x in range(children[0], children[2] + 1):
                    for y in range(children[1], children[3] + 1):
                        path = os.path.join(tiledir, str(z + 1), str(x), f"{y}.webp")
                        if os.path.exists(path) or _in_rects(dirty[z + 1], x, y):
                            continue
                        if (data := reader.get(z + 1, x, y)) is not None:
                            os.makedirs(os.path.dirname(path), exist_ok=True)
                            with open(path, "wb") as out:
                                out.write(data)
                            copied.append(path)
                subprocess.run(_tile_argv(base_vrt, tiledir, base_resampling, z, z + 1, "--resume",
                                          *_range_args(children)), env=env, check=True)
                for path in copied:
                    os.remove(path)


def _plan(tier: str, sids: list[str], env: dict, build: dict, incremental: bool,
          etags: dict[str, str]) -> tuple[dict, list[list[float]] | None]:
    """(the members record for the Item, the footprints to render again: the changed members' old
    and new ones). The footprints are None for a full build."""
    etag = {s: etags.get(f"{identity.COG_PREFIX}/{s}.cog.tif", "") for s in sids}
    prev, changed = None, None
    if incremental:
        item_id = f"geologic-maps-{tier}"
        prev = stac.prior_property(COLLECTION, item_id, MEMBERS_PROP)
        changed = changed_members(prev, stac.prior_property(COLLECTION, item_id, BUILD_PROP),
                                  etag, build)
        if changed is None:
            print(f"[mosaics] {tier}: full build (no matching record, or over "
                  f"{INCREMENTAL_MAX_FRACTION:.0%} of the members changed)")
    fresh = _cog_bboxes([s for s in sids if changed is None or s in changed], env)
    members = {s: {"etag": etag[s], "bbox": fresh[s] if s in fresh else prev[s]["bbox"]}
               for s in sids}
    if changed is None:
        return members, None
    boxes = [r["bbox"] for s in changed for r in (prev.get(s), members.get(s)) if r]
    if any(b is None for b in boxes):
        print(f"[mosaics] {tier}: full build (a changed member has no footprint)")
        return members, None
    if changed:
        print(f"[mosaics] {tier}: incremental, {len(changed)} changed: {', '.join(changed)}")
    return members, boxes


def _fetch_published(tier: str, tmp: str, objs: tuple[str, str], cog_rect: Rect,
                     cog_zoom: int) -> tuple[str, str] | None:
    """The published PMTiles and master, downloaded, or None when either is missing or unusable."""
    paths = (os.path.join(tmp, "published.pmtiles"), os.path.join(tmp, f"{tier}.master.tif"))
    why = None
    try:
        for obj, path in zip(objs, paths):
            gcs.download(obj, path)
    except FileNotFoundError as e:
        why = f"missing {e}"
    else:
        with open(paths[0], "rb") as f:
            clustered = Reader(MmapSource(f)).header()["clustered"]
        m = _master_rect(paths[1], cog_zoom)
        if not clustered:
            why = "the published PMTiles is not clustered"
        elif not (m[0] <= cog_rect[0] and m[1] <= cog_rect[1]
                  and cog_rect[2] <= m[2] and cog_rect[3] <= m[3]):
            why = "the mosaic outgrew the master"
    if why is None:
        return paths
    print(f"[mosaics] {tier}: full build ({why})")
    for p in paths:
        if os.path.exists(p):
            os.remove(p)
    return None


def build_tier(tier: str, sids: list[str], by_sid: dict[str, dict], maxz: int | None = None,
               *, suffix: str = "", write_stac_item: bool = True,
               published: PublishedPubs | None = None, full: bool = False,
               etags: dict[str, str] | None = None) -> bool:
    """Stitch one tier's COGs into a raster PMTiles and upload it. Returns False if the tier is
    empty. `suffix` (e.g. "-test-current") routes a scoped/--quads demo build to its own object,
    never the real tier — see `mosaic_object`. `write_stac_item=False` (the scoped/--quads path)
    uploads the scratch pmtiles for direct inspection but writes NO STAC item, see `build()`, and is
    always a full build. `full` skips the incremental path. `published` (see `published_pubs`) and
    `etags` ({object path: etag} of the COGs) are read when not given."""
    if not sids:
        print(f"[mosaics] {tier}: no COGs — skipping")
        return False
    env = gdal_env()
    sids = tier_members(tier, sids, by_sid, env)
    if not sids:
        print(f"[mosaics] {tier}: no 8-bit COGs left — skipping", file=sys.stderr)
        return False
    with tempfile.TemporaryDirectory(dir=MOSAIC_WORK_DIR) as tmp:
        listfile = os.path.join(tmp, "cogs.txt")
        members = [_vsigs(s) for s in sids]
        with open(listfile, "w") as fh:
            fh.write("\n".join(members) + "\n")
        tiledir = os.path.join(tmp, f"{tier}-tiles")
        pmtiles = os.path.join(tmp, f"{tier}.pmtiles")

        # `-resolution highest` keeps the VRT at the finest member COG's m/px, so at the mosaic's
        # native zoom the tile-grid-aligned masters tile 1:1.
        print(f"[mosaics] {tier}: VRT over {len(sids)} COGs")
        vrt = _build_vrt(listfile, os.path.join(tmp, f"{tier}.vrt"), members, env)
        native, aligned, bounds = _vrt_zoom_and_bounds(vrt, env)
        base_maxz = maxz if maxz is not None else native
        grid_vrts = {native: vrt} if aligned else {}    # VRTs on a zoom's tile grid, by zoom

        def grid_vrt(zoom: int) -> str:
            if zoom not in grid_vrts:
                print(f"[mosaics] {tier}: VRT on the z{zoom} grid")
                grid_vrts[zoom] = _build_vrt(listfile, os.path.join(tmp, f"{tier}-z{zoom}.vrt"),
                                             members, env, zoom)
            return grid_vrts[zoom]

        # `gdal raster tile` (GDAL 3.11+) tiles the VRT multithreaded and builds the overview pyramid in
        # one pass. At or above native it tiles the native VRT, a pixel-exact `nearest` copy only when
        # that VRT is on the grid; a cap below native tiles the cap's grid VRT, which copies through.
        tile_vrt = vrt if base_maxz >= native else grid_vrt(base_maxz)
        base_resampling = "nearest" if aligned or tile_vrt != vrt else "average"
        minz_arg = min(MOSAIC_MINZOOM, base_maxz)      # never emit --min-zoom > --max-zoom
        cog_zoom = min(MOSAIC_COG_MAXZOOM, base_maxz, native)
        cog_rect = _tile_range(bounds, cog_zoom)
        obj = mosaic_object(tier, suffix=suffix)
        cog_obj = mosaic_object(tier, suffix=suffix, ext="cog.tif")
        master_obj = mosaic_object(tier, suffix=suffix, ext="master.tif")
        record: dict = {}
        boxes = None
        if write_stac_item:
            build_sig = {"minzoom": minz_arg, "maxzoom": base_maxz, "cog_zoom": cog_zoom,
                         "native": native, "aligned": aligned, "quality": TILE_QUALITY}
            if etags is None:
                etags = gcs.list_etags(identity.COG_PREFIX, bucket=config.SOURCE_BUCKET)
            members_rec, boxes = _plan(tier, sids, env, build_sig, not full, etags)
            record = {MEMBERS_PROP: members_rec, BUILD_PROP: build_sig}
        if boxes == []:
            print(f"[mosaics] {tier}: no member changed — nothing to rebake")
            return True
        prior = _fetch_published(tier, tmp, (obj, master_obj), cog_rect, cog_zoom) if boxes else None
        master = os.path.join(tmp, f"{tier}.master.tif")

        if prior:
            dirty = dirty_rects(boxes, minz_arg, base_maxz)
            print(f"[mosaics] {tier}: rendering the changed tiles, z{minz_arg}-{base_maxz}")
            _render_dirty(dirty, tiledir, tile_vrt, base_resampling, base_maxz, prior[0], env)
            print(f"[mosaics] {tier}: merging them into the published PMTiles")
            n_tiles, minz, maxz_built = _pack(pmtiles, _merged_tiles(prior[0], tiledir, dirty),
                                              bounds=bounds, name=f"geologic-maps-{tier}")
            os.remove(prior[0])
        else:
            zdesc = f"z{minz_arg}-{base_maxz}" + ("" if maxz is not None else " (native)")
            print(f"[mosaics] {tier}: gdal raster tile -> {zdesc}, WebP q{TILE_QUALITY}, "
                  f"base={base_resampling} (native z{native})")
            subprocess.run(_tile_argv(tile_vrt, tiledir, base_resampling, minz_arg, base_maxz),
                           env=env, check=True)
            print(f"[mosaics] {tier}: writing tiles -> PMTiles")
            n_tiles, minz, maxz_built = _write_pmtiles(
                tiledir, pmtiles, bounds=bounds, name=f"geologic-maps-{tier}")
        shutil.rmtree(tiledir, ignore_errors=True)
        print(f"[mosaics] {tier}: {n_tiles} tiles (z{minz}-{maxz_built})")

        size_mb = os.path.getsize(pmtiles) // 1024 // 1024
        print(f"[mosaics] {tier}: uploading {size_mb} MB -> {obj}")
        gcs.upload(pmtiles, obj, content_type=PMTILES_MIME, cache_control=gcs.CACHE_MUTABLE)
        os.remove(pmtiles)

        # The COG is encoded from a lossless master, so a patched master re-encodes its unchanged
        # blocks to the same bytes; WebP COG blocks themselves cannot be rewritten in place.
        if prior:
            print(f"[mosaics] {tier}: patching the master on the z{cog_zoom} grid")
            _patch_master(grid_vrt(cog_zoom), master, dirty_rects(boxes, cog_zoom, cog_zoom)[cog_zoom],
                          cog_zoom, env, tmp)
        else:
            area = stac.union_bbox([bounds, UTAH_BBOX]) if write_stac_item else bounds
            print(f"[mosaics] {tier}: master on the z{cog_zoom} grid")
            _write_master(grid_vrt(cog_zoom), master, _tile_range(area, cog_zoom), cog_zoom, env)
        cog = os.path.join(tmp, f"{tier}.cog.tif")
        print(f"[mosaics] {tier}: WebP COG -> z{cog_zoom}")
        _write_cog(master, cog, cog_zoom, env, cog_rect)
        if write_stac_item:
            gcs.upload(master, master_obj, content_type="image/tiff", cache_control=gcs.CACHE_MUTABLE)
        os.remove(master)
        print(f"[mosaics] {tier}: uploading {os.path.getsize(cog) // 1024 // 1024} MB -> {cog_obj}")
        meta = gcs.upload(cog, cog_obj, content_type=config.COG_MIME, cache_control=gcs.CACHE_MUTABLE)
        if write_stac_item:
            _write_item(tier, sids, obj, by_sid, bounds=bounds,
                        published=published if published is not None else published_pubs(),
                        cog=_cog_asset(cog, cog_obj, meta, TIER_LABEL.get(tier, tier)), record=record)
        else:
            print(f"[mosaics] {tier}: scoped build — no STAC item written (scratch); "
                  f"inspect tiles directly at {config.public_url(obj)}")
        print(f"[mosaics] {tier}: done -> {config.public_url(obj)}, {config.public_url(cog_obj)}")
        return True


def _public_href(object_path: str) -> str:
    """A STAC document's href in the PUBLIC catalog. Pub documents live only there, so a mosaic baked
    into the review catalog still links its members to the public CDN rather than to review paths
    that don't exist."""
    rel = object_path.removeprefix(config.STAC_PREFIX).lstrip("/")
    return f"{config.PUBLIC_CATALOG_URL.rsplit('/', 1)[0]}/{rel}"


class PublishedPubs(NamedTuple):
    """Publication layout paths (`<group>/<SERIES>/<SID>`) in the published catalog."""
    plates: frozenset[str]       # with a `<SID>_plate/` Item directory
    collections: frozenset[str]  # with a collection.json


def _public_stac_prefix() -> str:
    """The public catalog's object prefix in the source bucket (the CDN preserves object paths)."""
    return urllib.parse.urlparse(config.PUBLIC_CATALOG_URL).path.strip("/").rpartition("/")[0]


def published_pubs() -> PublishedPubs:
    """What the public catalog holds for each publication, from one listing of each publication
    group in the source bucket, so a review bake reads the published catalog too."""
    plates: set[str] = set()
    collections: set[str] = set()
    base = _public_stac_prefix()
    strip = f"{base}/" if base else ""
    for group in identity.PUB_GROUPS:
        for path in gcs.list_paths(f"{strip}{group}/", bucket=config.SOURCE_BUCKET):
            layout = stac.pub_layout_of(path.removeprefix(strip))
            if not layout:
                continue
            _, series, sid, rest = layout
            if rest == "collection.json" and sid:
                collections.add(f"{group}/{series}/{sid}")
            elif sid and rest.split("/")[0] == f"{sid}{stac.PLATE_SUFFIX}" and rest.count("/") == 1:
                plates.add(f"{group}/{series}/{sid}")
    return PublishedPubs(frozenset(plates), frozenset(collections))


def _member_doc(p: dict, published: PublishedPubs) -> tuple[str, str]:
    """(object path, media type) of the published document a member's mosaic link points at: its
    plate Item, its Collection when the plate is its only data file (no plate Item), or, for a
    publication not yet in the collection layout, its `<SID>/<SID>.json` Item."""
    from . import sink_stac  # function-level: sink_stac never imports geolmap_mosaics, no cycle

    coll = sink_stac.pub_collection_path(p)
    if coll in published.plates:
        return stac.item_object_path(coll, stac.plate_item_id(p.get("series_id") or "")), GEOJSON
    if coll in published.collections:
        return stac.collection_object_path(coll), "application/json"
    series, _, pub_id = coll.rpartition("/")
    return stac.item_object_path(series, pub_id), GEOJSON


def _write_item(tier: str, sids: list[str], obj: str, by_sid: dict[str, dict], *,
                bounds: list[float], published: PublishedPubs, cog: dict | None = None,
                record: dict | None = None) -> None:
    """STAC item for one mosaic tier, footprinted by the mosaic's real [W, S, E, N] `bounds`. The
    raster PMTiles is a `visual` pmtiles ASSET (a vector layer would be a web-map LINK instead) —
    that's how the viewer tells a raster mosaic from vector tiles. `cog` is the WebP COG's asset.

    The mosaic is derived by stitching the member COGs, so each member gets a STAC
    `rel:"derived_from"` link — the spec's provenance relation ("a STAC Entity that was used as
    input data in the creation of this Entity"), to the document `_member_doc` finds for it in
    the published catalog, routed through
    `sink_stac.pub_collection_path` rather than hardcoded to `ugs-publications` (a member can be
    foreign-published `ugs-external` or a Mining District File `ugs-mining-district-files`, and
    hardcoding would 404). A COG with no matching pub record has no item to link to and is skipped,
    but still counts toward `ugs:map_count` (it's physically stitched into the raster). The reverse
    map→mosaic direction is a generic `rel:"related"` on the pub side (sink_stac) — STAC defines no
    reverse of `derived_from`."""
    from . import identify  # function-level: it imports vectors

    label = TIER_LABEL.get(tier, tier)
    n_maps = len(sids)
    w, s, e, n = bounds
    units = identify.mosaic_asset(tier)
    derived_from: list[dict] = []
    for sid in sids:                      # sids are UPPER (from _cog_sids)
        p = by_sid.get(sid)
        if not p:
            print(f"[mosaics] {tier}: COG {sid} has no pub record — stitched into the raster but not linked (orphan)", file=sys.stderr)
            continue                      # COG present but no pub record -> no STAC item exists to link
        real_sid = (p.get("series_id") or "").strip()
        doc, media = _member_doc(p, published)
        derived_from.append({
            "rel": "derived_from",
            "href": _public_href(doc),
            "type": media,
            "title": (p.get("pub_name") or "").strip() or stac.prettify(real_sid)})

    item = stac.build_item(
        item_id=f"geologic-maps-{tier}",
        collection=COLLECTION,
        geometry={"type": "Polygon", "coordinates": [[
            [w, s], [e, s], [e, n], [w, n], [w, s]]]},
        bbox=[w, s, e, n],
        datetime_iso=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        properties={"title": f"Utah Geologic Maps — {label} seamless mosaic",
                    "ugs:scale": tier, "ugs:map_count": n_maps, "ugs:topic": "geologic",
                    **(record or {})},
        assets={"tiles": {"href": config.public_url(obj), "type": PMTILES_MIME,
                          "roles": ["visual"], "ugs:render": "raster",
                          "title": f"Raster PMTiles ({label})"},
                **({"visual": cog} if cog else {}),
                **({"units": units} if units else {})},
        extra_links=derived_from,
        proj_epsg=4326,
    )
    stac.write_item(item)


def build(scales: list[str], maxz: int | None = None,
          edition_mode: str = "current", quads: str | None = None, full: bool = False) -> int:
    """`edition_mode` ("current"|"all") and `quads` (comma-separated quad names) are threaded
    through to `_group_by_tier`; `full` forces full rebakes. A `--quads` build is a scoped
    demo/scratch run: it writes ONLY a `-test-{edition_mode}`-suffixed scratch pmtiles object (never the real tier), writes NO STAC
    item, and skips the catalog refresh — the suffix carries the edition mode so a current-only
    demo and an all-editions demo of the SAME quads can coexist instead of clobbering each other."""
    groups, by_sid = _group_by_tier(edition_mode=edition_mode, quads=quads)
    suffix = f"-test-{edition_mode}" if quads else ""
    write_stac_item = not bool(quads)
    if quads:
        print(f"[mosaics] SCOPED build (--quads={quads!r}, --editions={edition_mode}) -> "
              f"writing '*{suffix}' items only, real tiers untouched")
    built, rebaked = 0, []
    published = published_pubs() if write_stac_item else None
    etags = gcs.list_etags(identity.COG_PREFIX, bucket=config.SOURCE_BUCKET) if write_stac_item else None
    for tier in scales:
        # Statewide (full) builds cap at the tier's TIER_MAXZOOM to keep the tile count sane; a scoped
        # --quads build renders at native zoom (full detail on a few maps). An explicit --maxzoom wins.
        tier_maxz = maxz if maxz is not None else (None if quads else TIER_MAXZOOM.get(tier))
        if build_tier(tier, groups.get(tier, []), by_sid, maxz=tier_maxz,
                      suffix=suffix, write_stac_item=write_stac_item, published=published,
                      full=full, etags=etags):
            built += 1
            rebaked.append(f"geologic-maps-{tier}")
    if built and not quads:
        # The COG is rewritten at the same URL, so the ImageServer's cached answers must go.
        cdn.mark(rebaked)
        print("[mosaics] refreshing STAC catalog")
        problems = stac.refresh_catalog()
        try:
            cdn.flush(problems)
        except Exception as e:  # noqa: BLE001 - the mosaics are written; the ids stay pending
            print(f"[mosaics] CDN invalidation FAILED, {rebaked} stay pending: {e}", file=sys.stderr)
    print(f"[mosaics] complete: {built}/{len(scales)} tiers built")
    return built


def main() -> int:
    ap = argparse.ArgumentParser(description="Build per-scale raster PMTiles + COG mosaics of geologic maps")
    ap.add_argument("--scale", choices=(*TIERS, "all"), action="append",
                    help="Scale tier to build; repeat for several (default all). The statewide 24k and "
                         "100k tiers run on Cloud Batch (scripts/submit_mosaics_batch.sh).")
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
    ap.add_argument("--full", action="store_true",
                    help="Rebake every tile, not only those under the members that changed.")
    args = ap.parse_args()
    # The PMTiles writer stages tile data in a tempfile; keep it on the work disk with the rest.
    tempfile.tempdir = MOSAIC_WORK_DIR
    if args.quads is not None and not any(q.strip() for q in args.quads.split(",")):
        ap.error("--quads contained no usable quad names")
    picked = args.scale or ["all"]
    scales = list(TIERS) if "all" in picked else list(dict.fromkeys(picked))
    built = build(scales, maxz=args.maxzoom, edition_mode=args.editions, quads=args.quads,
                  full=args.full)
    # A scoped --quads build under the default --scale all legitimately leaves tiers with no members.
    return 0 if built == len(scales) or (args.quads and built > 0) else 1


if __name__ == "__main__":
    sys.exit(main())
