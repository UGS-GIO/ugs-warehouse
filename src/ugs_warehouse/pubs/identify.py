"""Identify layers for the map mosaics: one vector PMTiles of geologic unit polygons per mosaic tier,
so a click on the raster can name the unit under it.

`<vectors prefix>/_identify/units-<tier>.pmtiles` holds one tile layer, `units`, with the attributes
series_id, unit_code, unit_name, age, description and source_label. Its sources, per tier member in
the mosaic's draw order:

- the member's own `unit_polys` (pubs/unit_polys.py), the authoritative attributes;
- for a member without one, its polygons in the seamless layer `geolmap/units/units.parquet`
  (unit_symbol, unit_name, age; no description), picked by series_id.

Membership is the mosaic bake's (`geolmap_mosaics._group_by_tier`, then `tier_members`: the 8-bit
COGs in draw order), never derived here. Each polygon carries `rank`, its member's draw-order index.
`<vectors prefix>/_identify/<tier>.state.json` records the membership, each member's `unit_polys`
checksum and the seamless file's etag the last build used; a tier is built again only when one of
those changed. The vectors job runs `build_changed` after its self-heal, since it has tippecanoe and
DuckDB. The mosaic Item's `units` asset comes from `mosaic_asset`, which the mosaics job's item
writer calls and `build_changed` applies to an Item already published.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import urllib.parse
from dataclasses import dataclass, field

from ..core import config, gcs, stac
from ..vector import sink_pmtiles, transform
from . import identity, vectors
from .unit_polys import TILE_LAYER

# Bump to build every tier again at the next vectors run.
IDENTIFY_VERSION = 1

UNITS_TITLE = "Geologic units (identify)"
SEAMLESS_OBJECT = f"{identity.UNITS_PREFIX}/units.parquet"
SEAMLESS_LABEL = "seamless"
# Memory cap for the DuckDB exports; the sort over the seamless fallback rows spills to disk.
DUCKDB_MEMORY = "2GB"


def identify_prefix() -> str:
    return f"{vectors.VECTORS_PREFIX}/_identify"


def tiles_object(tier: str) -> str:
    return f"{identify_prefix()}/units-{tier}.pmtiles"


def state_object(tier: str) -> str:
    return f"{identify_prefix()}/{tier}.state.json"


def units_asset(object_path: str, meta: dict | None, *, origin: str | None = None) -> dict:
    """The `units` asset of a plate or mosaic Item: the identify PMTiles at `object_path`, with the
    recorded `meta` ({bytes, checksum}) as file fields. `origin` is the public site the file is
    served from when that is not this build's own CDN."""
    href = f"{origin}/{object_path.lstrip('/')}" if origin else config.public_url(object_path)
    return {"href": href, "type": config.PMTILES_MIME, "roles": ["data"],
            "title": UNITS_TITLE,
            **stac.file_fields(gcs.FileMeta(meta["bytes"], meta.get("checksum")) if meta else None)}


def plate_asset(series_id: str, unit_polys: dict | None) -> dict | None:
    """The plate Item's `units` asset: the pub's own `unit_polys` PMTiles, normalized to the
    identify attributes. None for a pub with no unit product."""
    if not unit_polys or not unit_polys.get("pmtiles"):
        return None
    return units_asset(vectors.unit_polys_pmtiles_object(series_id.upper()), unit_polys["pmtiles"])


def read_state(tier: str, *, bucket: str | None = None) -> dict | None:
    """The tier's state file in `bucket` (default BUCKET), or None when it has none. An unreadable
    one raises."""
    try:
        doc = json.loads(gcs.get_bytes(state_object(tier), bucket=bucket).decode())
    except FileNotFoundError:
        return None
    if not isinstance(doc, dict):
        raise ValueError(f"{state_object(tier)} is not a JSON object")
    return doc


def mosaic_asset(tier: str) -> dict | None:
    """The mosaic Item's `units` asset for `tier`, or None while no identify layer has been built.
    The vectors job writes the layer to the source bucket; a mosaic baked into another bucket (a
    review bake) links it by the public catalog's site, as it links its member documents."""
    state = read_state(tier, bucket=config.SOURCE_BUCKET)
    if not state or not state.get("pmtiles"):
        return None
    origin = None
    if config.SOURCE_BUCKET != config.BUCKET:
        parts = urllib.parse.urlsplit(config.PUBLIC_CATALOG_URL)
        origin = f"{parts.scheme}://{parts.netloc}"
    return units_asset(tiles_object(tier), state["pmtiles"], origin=origin)


def attach_to_mosaic_item(tier: str, asset: dict) -> bool:
    """Make the published mosaic Item of `tier` carry `asset` as `units`, patching it in place when
    it does not already. False when the mosaic has no Item yet; its next bake adds the asset
    through `mosaic_asset`."""
    from . import geolmap_mosaics

    path = stac.item_object_path(geolmap_mosaics.COLLECTION, f"geologic-maps-{tier}")
    try:
        item = json.loads(gcs.get_bytes(path).decode())
    except FileNotFoundError:
        return False
    if (item.get("assets") or {}).get("units") == asset:
        return True
    return stac.patch_published(path, {"units": asset}, target="assets")


def bands_object() -> str:
    return f"{identify_prefix()}/cog_bands.json"


def band_types(sids: list[str], env: dict) -> dict[str, list[str]]:
    """Each COG's band data types. The header reads are kept in `_identify/cog_bands.json`, keyed by
    the COG's etag from one listing, so only a COG that is new or rewritten is read again."""
    from . import geolmap_mosaics

    etags = gcs.list_etags(identity.COG_PREFIX, bucket=config.SOURCE_BUCKET)
    try:
        cache = json.loads(gcs.get_bytes(bands_object()).decode())
    except FileNotFoundError:
        cache = {}
    etag = {sid: etags.get(f"{identity.COG_PREFIX}/{sid}.cog.tif") for sid in sids}
    stale = [sid for sid in sids if not etag[sid] or (cache.get(sid) or {}).get("etag") != etag[sid]]
    if not stale:
        return {sid: cache[sid]["types"] for sid in sids}
    fresh = geolmap_mosaics._band_types(stale, env)
    kept = {sid: cache[sid] for sid in sids if sid not in stale}
    kept.update({sid: {"etag": etag[sid], "types": fresh[sid]} for sid in stale if etag[sid]})
    gcs.put_bytes(json.dumps(kept).encode(), bands_object(), content_type="application/json",
                  cache_control=gcs.CACHE_MUTABLE)
    return {sid: fresh[sid] if sid in fresh else kept[sid]["types"] for sid in sids}


def membership() -> dict[str, list[str]]:
    """{tier: member series ids (upper-case) in the mosaic's draw order} for each tier with members:
    the mosaic bake's own grouping, then `geolmap_mosaics.tier_members`, so a non-8-bit COG the bake
    leaves out is left out here."""
    from . import geolmap_mosaics

    groups, by_sid = geolmap_mosaics._group_by_tier()
    env = geolmap_mosaics.gdal_env()
    types = band_types(sorted({sid for sids in groups.values() for sid in sids}), env)
    out = {t: geolmap_mosaics.tier_members(t, sids, by_sid, env, {s: types[s] for s in sids})
           for t, sids in groups.items() if sids}
    return {t: order for t, order in out.items() if order}


def unit_checksums() -> tuple[dict[str, str], list[str]]:
    """({SID: unit_polys checksum} for each clean manifest that has the product, an error per
    manifest that could not be read), read once on the manifest scan's threads."""
    def reduce(_sid: str, doc: dict) -> str | None:
        return (doc.get("unit_polys") or {}).get("checksum") if vectors.is_clean(doc) else None

    sums: dict[str, str] = {}
    errors: list[str] = []
    for sid, scanned in vectors.scan_manifests(reduce).items():
        if scanned.error:
            errors.append(f"{sid}: unreadable manifest: {scanned.error}")
        elif scanned.value:
            sums[sid.upper()] = scanned.value
    return sums, errors


def seamless_etag() -> str | None:
    """The seamless file's etag, or None when it is missing. The listing is of its folder: a
    listing prefix matches whole path segments, so the file's own name would find nothing."""
    return gcs.list_etags(identity.UNITS_PREFIX).get(SEAMLESS_OBJECT)


def signature(order: list[str], sums: dict[str, str], seamless: str | None) -> dict:
    """What a tier's tiles are built from. A member with no `unit_polys` reads None and depends on
    the seamless file's etag."""
    sources = {sid: sums.get(sid) for sid in order}
    return {"version": IDENTIFY_VERSION, "members": order, "sources": sources,
            "seamless": seamless if any(v is None for v in sources.values()) else None}


def _export(con, view: str, path: str) -> int:
    """Write `view` (COLUMNS plus lon/lat `geom`) to a GeoJSONSeq file; returns its row count."""
    rows = con.execute(f"SELECT count(*) FROM {view}").fetchone()[0]
    if rows:
        sink_pmtiles.write_geojsonl(con, view, path)
    return rows


def _append(src: str, dest) -> None:
    with open(src, "rb") as fh:
        shutil.copyfileobj(fh, dest)
    os.remove(src)


def build_geojsonl(order: list[str], sums: dict[str, str], out: str, work: str) -> dict[str, int]:
    """Write the tier's polygons to the GeoJSONSeq file `out`, own `unit_polys` first in member order,
    then the seamless fallback rows of the members without one, in member order. Each polygon
    carries `rank`, its member's index in the draw order (the seamless fallback ranks like its
    member), so a viewer takes the highest rank at a point. Returns {"own": rows, "seamless": rows}."""
    import duckdb

    from .vectors import _literal

    own = [sid for sid in order if sums.get(sid)]
    fallback = [sid for sid in order if not sums.get(sid)]
    counts = {"own": 0, "seamless": 0}
    con = duckdb.connect()
    try:
        transform.setup(con)
        con.execute(f"SET max_memory={_literal(DUCKDB_MEMORY)}")
        con.execute(f"SET temp_directory={_literal(work)}")
        con.execute("SET TimeZone = 'UTC'")
        with open(out, "wb") as dest:
            for i, sid in enumerate(own):
                local = os.path.join(work, f"own_{i}.parquet")
                gcs.download(vectors.unit_polys_object(sid), local)
                con.execute(f"CREATE OR REPLACE TEMP VIEW piece AS SELECT *, {order.index(sid)} AS rank "
                            f"FROM read_parquet({_literal(local)})")
                chunk = os.path.join(work, f"own_{i}.geojsonl")
                counts["own"] += _export(con, "piece", chunk)
                con.execute("DROP VIEW piece")
                os.remove(local)
                if os.path.exists(chunk):
                    _append(chunk, dest)
            if fallback:
                seamless = os.path.join(work, "seamless.parquet")
                gcs.download(SEAMLESS_OBJECT, seamless)
                ids = ", ".join(_literal(s) for s in fallback)
                ranked = ", ".join(_literal(s) for s in order)
                clean = lambda col: f"NULLIF(TRIM(CAST({col} AS VARCHAR)), '')"  # noqa: E731
                con.execute(
                    f"CREATE OR REPLACE TEMP VIEW piece AS SELECT UPPER(series_id) AS series_id, "
                    f"{clean('unit_symbol')} AS unit_code, {clean('unit_name')} AS unit_name, "
                    f"{clean('age')} AS age, CAST(NULL AS VARCHAR) AS description, "
                    f"{_literal(SEAMLESS_LABEL)} AS source_label, ST_Force2D(geometry) AS geom, "
                    f"list_position([{ranked}], UPPER(series_id)) - 1 AS rank "
                    f"FROM read_parquet({_literal(seamless)}) "
                    f"WHERE UPPER(series_id) IN ({ids}) AND geometry IS NOT NULL "
                    f"ORDER BY rank")
                chunk = os.path.join(work, "seamless.geojsonl")
                counts["seamless"] = _export(con, "piece", chunk)
                con.execute("DROP VIEW piece")
                os.remove(seamless)
                if os.path.exists(chunk):
                    _append(chunk, dest)
    finally:
        con.close()
    return counts


def build_tier(tier: str, order: list[str], sums: dict[str, str], sig: dict) -> dict | None:
    """Build and upload the tier's identify PMTiles. Returns the state to record, or None when no
    member has any polygon (nothing is written). The tiles cover the zooms the mosaic shows, up to
    the tier's top zoom."""
    from . import geolmap_mosaics

    maxz = geolmap_mosaics.TIER_MAXZOOM[tier]
    minz = min(geolmap_mosaics.MOSAIC_MINZOOM, maxz)
    with tempfile.TemporaryDirectory(prefix=f"identify_{tier}_") as work:
        geojsonl = os.path.join(work, "units.geojsonl")
        counts = build_geojsonl(order, sums, geojsonl, work)
        if not counts["own"] + counts["seamless"]:
            return None
        pmtiles = os.path.join(work, f"units-{tier}.pmtiles")
        sink_pmtiles.run_tippecanoe(geojsonl, pmtiles, TILE_LAYER, what=f"identify/{tier}",
                                    extra_opts=["-Z", str(minz), "-z", str(maxz), "-t", work,
                                                *sink_pmtiles.IDENTIFY_OPTS])
        os.remove(geojsonl)
        meta = gcs.upload(pmtiles, tiles_object(tier), content_type=config.PMTILES_MIME,
                          cache_control=gcs.CACHE_MUTABLE)
    return {**sig, "pmtiles": {"bytes": meta.size, "checksum": meta.checksum}, "features": counts,
            "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def write_state(tier: str, state: dict) -> None:
    gcs.put_bytes(json.dumps(state).encode(), state_object(tier), content_type="application/json",
                  cache_control=gcs.CACHE_MUTABLE)


@dataclass
class Result:
    built: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    empty: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def build_changed(tiers: list[str] | None = None) -> Result:
    """Build the identify layer of each tier whose membership, member `unit_polys` or seamless
    fallback source changed since its state file, and put the `units` asset on its published mosaic
    Item. A tier that fails is an error in the result and the others still run."""
    result = Result()
    try:
        groups = membership()
        sums, errors = unit_checksums()
    except Exception as e:  # noqa: BLE001 (returned; the run reports it and exits non-zero)
        result.errors.append(f"identify membership: {type(e).__name__}: {e}")
        print(f"[identify] FAIL membership: {type(e).__name__}: {e}", file=sys.stderr)
        return result
    result.errors += errors
    etag = None
    for tier, order in sorted(groups.items()):
        if tiers is not None and tier not in tiers:
            continue
        try:
            if any(sid not in sums for sid in order) and etag is None:
                etag = seamless_etag()
                if etag is None:
                    raise FileNotFoundError(f"{SEAMLESS_OBJECT} is missing, and {tier} has members "
                                            "without their own unit polygons")
            sig = signature(order, sums, etag)
            state = read_state(tier)
            current = state is not None and {k: state.get(k) for k in sig} == sig
            if current:
                # A patch that failed last run left the Item without the asset: retry it.
                attach_to_mosaic_item(tier, units_asset(tiles_object(tier), state["pmtiles"]))
                result.unchanged.append(tier)
                continue
            state = build_tier(tier, order, sums, sig)
            if state is None:
                result.empty.append(tier)
                print(f"[identify] {tier}: no member has unit polygons; nothing built", file=sys.stderr)
                continue
            # The state is recorded only once the Item carries the asset, so a failed patch rebuilds
            # (and patches) at the next run instead of being taken for done.
            attach_to_mosaic_item(tier, units_asset(tiles_object(tier), state["pmtiles"]))
            write_state(tier, state)
            result.built.append(tier)
            print(f"[identify] {tier}: built from {len(order)} members "
                  f"({state['features']['own']} own polygons, {state['features']['seamless']} seamless)")
        except Exception as e:  # noqa: BLE001 (returned per tier; the run reports it and exits non-zero)
            result.errors.append(f"identify {tier}: {type(e).__name__}: {e}")
            print(f"[identify] {tier}: FAIL {type(e).__name__}: {e}", file=sys.stderr)
    return result
