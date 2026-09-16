"""Build per-scale seamless RASTER mosaics of the published geologic maps as raster PMTiles.

The static-CDN equivalent of the old geolMapPortal ArcGIS Mosaic Datasets (MD_500K / MD_250K /
MD_24K ImageServers): the per-map COGs (geolmap/cogs/<series_id>.cog.tif, from pubs/harvest.py) are
grouped by their publication scale into three tiers and stitched into one raster PMTiles per tier —
geolmap/mosaics/geologic-maps-{tier}.pmtiles. The viewer toggles them like the old portal.

Pipeline per tier (GDAL, no full download — COGs are read in place over /vsigs):
  gdalbuildvrt (over /vsigs/<bucket>/...)  ->  gdal_translate -of MBTILES (lossless PNG tiles)
  ->  gdaladdo (overview = lower zooms)  ->  `pmtiles convert` (MBTiles -> PMTiles)  ->  upload

Source COGs are currently WebP-lossy (harvest COG_COMPRESS); tiles are encoded as PNG (lossless) so
this step adds NO further loss. If the harvest later switches to lossless COGs, just re-run this job.

By default (`--editions current`) a superseded edition of a quad (per `editions.py`'s edition
graph) is dropped before the VRT — the mosaic shows one current map per quad. `--editions all`
reproduces the old behavior (every COG, every edition stacked). `--quads` scopes a build to named
quads and writes it to `*-test-current`/`*-test-all` items/objects (the edition mode is baked into
the suffix), so a current-only demo and an all-editions demo of the SAME quads can be built side
by side for comparison without either clobbering the other or the real tier:

    python -m ugs_warehouse.pubs.geolmap_mosaics --scale all
    python -m ugs_warehouse.pubs.geolmap_mosaics --scale 24k --editions all
    python -m ugs_warehouse.pubs.geolmap_mosaics --scale 24k --quads "Park City East Quad" --editions current
    python -m ugs_warehouse.pubs.geolmap_mosaics --scale 24k --quads "Park City East Quad" --editions all
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

from ..core import config, gcs, stac
from . import editions, identity, source
from .scale import DEFAULT_TIER, SCALE_LABEL, tier_of

PMTILES_MIME = config.PMTILES_MIME
# Each tier is one STAC item in this collection; the viewer toggles them like the old portal layers.
COLLECTION = "ugs-geologic-maps"
# Statewide extent (the mosaics are clipped to Utah). [W, S, E, N] in EPSG:4326.
UTAH_BBOX = [-114.053, 36.998, -109.041, 42.002]
UTAH_GEOM = {"type": "Polygon", "coordinates": [[
    [-114.053, 36.998], [-109.041, 36.998], [-109.041, 42.002],
    [-114.053, 42.002], [-114.053, 36.998]]]}
# Scale tiers (denominator upper bounds), matching the old MD_* mosaics. A map's scale is binned by
# its 1:N denominator: <=62.5k detail, <=350k intermediate, else overview.
TIERS = ("24k", "250k", "500k")
# Max web-mercator zoom per tier — the real fix for the build timeout. The COGs are 600 DPI, so
# GDAL's native max zoom is ~z18; tiling a STATEWIDE mosaic to z18 is astronomically many tiles and
# never finishes. Each tier is capped to the zoom its scale actually warrants (and where it's legible
# in the viewer): a 1:500k map adds nothing past ~z12, 24k past ~z14. Override with --maxzoom.
TIER_MAXZOOM = {"24k": 14, "250k": 12, "500k": 12}
# How far down to build overviews (lower zoom levels) off the capped base tiles.
OVERVIEW_LEVELS = ("2", "4", "8", "16", "32", "64", "128", "256", "512", "1024", "2048")


def mosaic_object(tier: str, *, suffix: str = "") -> str:
    return f"{identity.MOSAIC_PREFIX}/geologic-maps-{tier}{suffix}.pmtiles"


def _cog_sids() -> set[str]:
    """Series ids that have a harvested COG (geolmap/cogs/<sid>.cog.tif)."""
    pfx = identity.COG_PREFIX.rstrip("/") + "/"
    out: set[str] = set()
    for path in gcs.list_paths(identity.COG_PREFIX):
        name = path[len(pfx):] if path.startswith(pfx) else path
        if name.endswith(".cog.tif"):
            out.add(name[: -len(".cog.tif")].upper())
    return out


def _group_by_tier(edition_mode: str = "current",
                    quads: str | None = None) -> tuple[dict[str, list[str]], dict[str, dict]]:
    """{tier -> [series_id, ...]} for every map that has a COG, binned by its publication scale —
    plus {UPPER series_id -> pub record}, so `_write_item` can build member links without a
    second `source.read_pubs()` pass.

    `edition_mode`: "current" (default) drops superseded quad editions before the tier bins fill,
    using the edition graph from `editions.py` — the authoritative mosaic should show one map per
    quad, not every historical revision stacked on top of each other. "all" reproduces the legacy
    behavior (every COG, every edition) and skips the footprints/edition-graph read entirely (no
    reason to pay for a CDN round trip when nothing will be filtered).

    `quads`: comma-separated quad names (matched casefolded) restricting membership to those
    quads — the scoped/demo build path driven by `--quads` in `main()`.

    The footprints quad map is loaded AT MOST once and reused for both the deprecation graph and
    the `--quads` filter, whichever of the two apply."""
    pubs_by_sid = {(p.get("series_id") or "").strip().upper(): p for p in source.read_pubs()}

    qmap: dict[str, str] = {}
    if edition_mode == "current" or quads:
        qmap = editions.quad_by_series()

    deprecated_upper: set[str] = set()
    if edition_mode == "current":
        graph = editions.edition_graph(list(pubs_by_sid.values()), quad_by_sid=qmap)
        deprecated_upper = {s.upper() for s, e in graph.items() if e["deprecated"]}

    requested_quads: set[str] | None = None
    if quads:
        requested_quads = {q.strip().casefold() for q in quads.split(",") if q.strip()}

    groups: dict[str, list[str]] = {t: [] for t in TIERS}
    unparsed = 0
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
        raw_scale = (pubs_by_sid.get(sid, {}).get("pub_scale") or "")
        t = tier_of(raw_scale) or DEFAULT_TIER
        if tier_of(raw_scale) is None:
            unparsed += 1
        groups[t].append(sid)
    counts = ", ".join(f"{t}={len(groups[t])}" for t in TIERS)
    detail = f"unparseable -> {DEFAULT_TIER}: {unparsed}"
    if edition_mode == "current":
        detail += f", deprecated editions dropped: {n_deprecated}"
    if requested_quads is not None:
        detail += f", outside --quads: {n_outside_quads}"
    print(f"[mosaics] COGs grouped by scale: {counts}  ({detail})")
    return groups, pubs_by_sid


def _vsigs(sid: str) -> str:
    """GDAL /vsigs path to a COG — read in place from the (private) bucket, no download."""
    return f"/vsigs/{config.BUCKET}/{identity.COG_PREFIX}/{sid}.cog.tif"


def build_tier(tier: str, sids: list[str], by_sid: dict[str, dict], maxz: int | None = None,
                *, suffix: str = "") -> bool:
    """Stitch one tier's COGs into a raster PMTiles and upload it. Returns False if the tier is
    empty. `suffix` (e.g. "-test-current") routes a scoped/--quads demo build to its own object +
    item, never the real tier — see `mosaic_object`/`_write_item`."""
    if not sids:
        print(f"[mosaics] {tier}: no COGs — skipping")
        return False
    with tempfile.TemporaryDirectory() as tmp:
        listfile = os.path.join(tmp, "cogs.txt")
        with open(listfile, "w") as fh:
            fh.write("\n".join(_vsigs(s) for s in sids) + "\n")
        vrt = os.path.join(tmp, f"{tier}.vrt")
        mbtiles = os.path.join(tmp, f"{tier}.mbtiles")
        pmtiles = os.path.join(tmp, f"{tier}.pmtiles")

        # Performance-tuned GDAL environment variables for high-throughput cloud storage reading.
        gdal_env = os.environ.copy()
        gdal_env.update({
            "GDAL_CACHEMAX": "4096",                         # Use 4 GB cache (out of 16 GB available on runner)
            "GDAL_NUM_THREADS": "ALL_CPUS",                  # Parallelize tile rendering and compression
            "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",     # Prevent redundant sequential GCS directory scans
            "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.tiff,.vrt", # Limit seeking of non-existent sidecar files
            "VSI_CACHE": "TRUE",                             # Enable GDAL VSI file caching
            "VSI_CACHE_SIZE": "536870912",                   # 512 MB chunk cache for remote files
            "GDAL_HTTP_MAX_RETRY": "10",                     # Keep connections resilient against transient hiccups
            "GDAL_HTTP_RETRY_DELAY": "1",
        })

        print(f"[mosaics] {tier}: VRT over {len(sids)} COGs")
        subprocess.run(["gdalbuildvrt", "-q", "-addalpha", "-input_file_list", listfile, vrt],
                       env=gdal_env, check=True)

        # Lossless PNG tiles (alpha → transparent gaps where no map covers). Cap the base zoom to the
        # tier's level (--maxzoom overrides) — without this the 600 DPI native zoom blows the build up.
        mz = maxz if maxz is not None else TIER_MAXZOOM.get(tier)
        tr = ["gdal_translate", "-of", "MBTILES", "-r", "bilinear", "-co", "TILE_FORMAT=PNG"]
        if mz is not None:
            tr += ["-co", f"ZOOM_LEVEL={mz}"]
        print(f"[mosaics] {tier}: rendering base tiles -> MBTiles (max zoom {mz})")
        subprocess.run([*tr, vrt, mbtiles], env=gdal_env, check=True)

        print(f"[mosaics] {tier}: building overviews (lower zooms)")
        subprocess.run(["gdaladdo", "-r", "bilinear", mbtiles, *OVERVIEW_LEVELS], env=gdal_env, check=True)

        print(f"[mosaics] {tier}: MBTiles -> PMTiles")
        subprocess.run(["pmtiles", "convert", mbtiles, pmtiles], check=True)

        obj = mosaic_object(tier, suffix=suffix)
        size_mb = os.path.getsize(pmtiles) // 1024 // 1024
        print(f"[mosaics] {tier}: uploading {size_mb} MB -> {obj}")
        gcs.upload(pmtiles, obj, content_type=PMTILES_MIME, cache_control=gcs.CACHE_MUTABLE)
        _write_item(tier, sids, obj, by_sid, suffix=suffix)
        print(f"[mosaics] {tier}: done -> {config.public_url(obj)}")
        return True


def _write_item(tier: str, sids: list[str], obj: str, by_sid: dict[str, dict],
                 *, suffix: str = "") -> None:
    """STAC item for one mosaic tier. The raster PMTiles is a `visual` pmtiles ASSET (a vector layer
    would be a web-map LINK instead) — that's how the viewer tells a raster mosaic from vector tiles.

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

    label = SCALE_LABEL.get(tier, tier)
    n_maps = len(sids)
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
            "href": config.public_url(stac.item_object_path(coll, sink_stac.item_id_for(real_sid))),
            "type": "application/geo+json",
            "title": (p.get("pub_name") or "").strip() or stac.prettify(real_sid)})

    item = stac.build_item(
        item_id=f"geologic-maps-{tier}{suffix}",
        collection=COLLECTION,
        geometry=UTAH_GEOM, bbox=UTAH_BBOX,
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
    through to `_group_by_tier`. A `--quads` build is a scoped demo/scratch run: it writes to
    `-test-{edition_mode}`-suffixed items/objects (never the real tier) and skips the catalog
    refresh — the suffix carries the edition mode so a current-only demo and an all-editions demo
    of the SAME quads can coexist instead of clobbering each other."""
    groups, by_sid = _group_by_tier(edition_mode=edition_mode, quads=quads)
    suffix = f"-test-{edition_mode}" if quads else ""
    if quads:
        print(f"[mosaics] SCOPED build (--quads={quads!r}, --editions={edition_mode}) -> "
              f"writing '*{suffix}' items only, real tiers untouched")
    built = 0
    for tier in scales:
        if build_tier(tier, groups.get(tier, []), by_sid, maxz=maxz, suffix=suffix):
            built += 1
    if built and not quads:
        print("[mosaics] refreshing STAC catalog")
        stac.refresh_catalog()
    print(f"[mosaics] complete: {built}/{len(scales)} tiers built")
    return built


def main() -> int:
    ap = argparse.ArgumentParser(description="Build per-scale raster PMTiles mosaics of geologic maps")
    ap.add_argument("--scale", choices=(*TIERS, "all"), default="all",
                    help="Which scale tier to build (default all). 24k is the largest — run it alone "
                         "with more memory if needed.")
    ap.add_argument("--maxzoom", type=int, default=None,
                    help="Cap the base (native) zoom level; default lets GDAL pick from resolution.")
    ap.add_argument("--editions", choices=("current", "all"), default="current",
                    help="current (default) drops superseded quad editions before stitching; "
                         "all reproduces the legacy behavior (every COG, every edition).")
    ap.add_argument("--quads", default=None,
                    help="Comma-separated quad names (as in the footprints quad_name) to restrict "
                         "members to — a scoped demo/scratch build; writes to *-test-current or "
                         "*-test-all (matching --editions) instead of the real tier.")
    args = ap.parse_args()
    if args.quads is not None and not any(q.strip() for q in args.quads.split(",")):
        ap.error("--quads contained no usable quad names")
    scales = list(TIERS) if args.scale == "all" else [args.scale]
    return 0 if build(scales, maxz=args.maxzoom, edition_mode=args.editions,
                       quads=args.quads) >= 0 else 1


if __name__ == "__main__":
    sys.exit(main())
