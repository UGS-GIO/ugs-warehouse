"""Build the `ugs-publications` STAC collection from pub metadata + harvested artifacts.

Every pub -> a STAC item (download links always; COG / units / thumbnail / footprint geometry
where present). Harvested-artifact presence is derived by listing GCS once (not a HEAD per
pub). Then `core.stac.refresh_catalog()` rebuilds the root + collections — so the pubs land
in the SAME catalog as the vector serving topics.

    python -m ugs_warehouse.pubs.ingest            # all pubs
    python -m ugs_warehouse.pubs.ingest --limit 50 # first N (smoke)
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from concurrent.futures import ThreadPoolExecutor

from ..core import config, gcs, stac
from . import editions, identity, sink_stac, source, threed, topic, vectors


def _names(prefix: str) -> list[str]:
    return [path.rsplit("/", 1)[-1] for path in gcs.list_paths(prefix)]


def _ids(names: list[str], suffix: str) -> set[str]:
    return {name[: -len(suffix)].upper() for name in names if name.endswith(suffix)}


def _ids_with_suffix(prefix: str, suffix: str) -> set[str]:
    return _ids(_names(prefix), suffix)


def _image_ids() -> tuple[set[str], set[str]]:
    """(pubs with a harvested map thumbnail, pubs with a cover). Only the WebPs count, since the items
    point at them. A pub whose preview exists only as a PNG is named in a warning, because its item
    goes out without that preview until webp_backfill converts it."""
    plates, covered = _names(identity.COG_PREFIX), _names(identity.PUB_THUMB_PREFIX)
    thumbs = _ids(plates, identity.COG_THUMB_SUFFIX)
    covers = _ids(covered, identity.COVER_SUFFIX)
    png_only = sorted((_ids(plates, identity.PNG_THUMB_SUFFIX) - thumbs)
                      | (_ids(covered, identity.PNG_COVER_SUFFIX) - covers))
    if png_only:
        print(f"[pubs] WARNING: {len(png_only)} pub(s) have a PNG preview but no WebP, so their items "
              f"go out without it; run pubs.webp_backfill --apply. {', '.join(png_only[:10])}",
              file=sys.stderr)
    return thumbs, covers


def _contents_by_sid() -> dict[str, list[dict]]:
    """{SID: [{title, page}, …]} from the Survey Notes TOC sidecars (parsed or hand-authored)."""
    paths = [p for p in gcs.list_paths(identity.PUB_CONTENTS_PREFIX) if p.endswith(".json")]

    def load(path: str) -> tuple[str, list[dict]]:
        sid = path.rsplit("/", 1)[-1][: -len(".json")].upper()
        try:
            return sid, (json.loads(gcs.get_bytes(path).decode()).get("contents") or [])
        except Exception:  # noqa: BLE001 — a bad sidecar just means no panel for that issue
            return sid, []

    out: dict[str, list[dict]] = {}
    with ThreadPoolExecutor(max_workers=16) as ex:
        for sid, toc in ex.map(load, paths):
            if toc:
                out[sid] = toc
    return out


def _overrides_by_sid() -> dict[str, dict]:
    """{SID: override doc} from the hand-authored metadata overrides (overrides/{ID}.json). Preloaded
    once (operators backfill only a handful) so build_item doesn't do a GCS read per pub."""
    out: dict[str, dict] = {}
    for path in gcs.list_paths(config.OVERRIDES_PREFIX):
        if not path.endswith(".json"):
            continue
        sid = path.rsplit("/", 1)[-1][: -len(".json")].upper()
        try:
            out[sid] = json.loads(gcs.get_bytes(path).decode())
        except Exception:  # noqa: BLE001
            continue
    return out


def _threed_classes_by_sid() -> dict[str, list[dict]]:
    """{SID: classification:classes} from the 3D classes sidecars the convert step writes next to the
    GeoParquet (geolmap/3d/{SID}_3d_classes.json). Read here so a pubs rebuild re-stamps the authored
    fence colors without re-running the .mapx conversion."""
    suffix = f"_{threed.CLASSES_NAME}"  # "_3d_classes.json"
    paths = [p for p in gcs.list_paths(identity.THREED_PREFIX) if p.endswith(suffix)]

    def load(path: str) -> tuple[str, list[dict]]:
        sid = path.rsplit("/", 1)[-1][: -len(suffix)].upper()
        try:
            return sid, (json.loads(gcs.get_bytes(path).decode()) or [])
        except Exception:  # noqa: BLE001
            return sid, []

    out: dict[str, list[dict]] = {}
    with ThreadPoolExecutor(max_workers=16) as ex:
        for sid, classes in ex.map(load, paths):
            if classes:
                out[sid] = classes
    return out


def _mirrored_files() -> set[str]:
    """Object paths of the publication source files we hold a copy of (written by pubs/mirror.py).

    One listing, not a HEAD per asset: the object path IS the legacy URL's path (#120), so plain
    set membership is everything `build_item` needs to decide which assets serve from our CDN.
    """
    return set(gcs.list_paths(identity.PUB_FILES_PREFIX))


def _unit_ids() -> set[str]:
    """Series ids that have a per-map units sidecar (geolmap/units/<series_id>/…). Only directory
    segments count — files sitting at the prefix root (the statewide units.pmtiles / units.parquet)
    are NOT series ids and must be skipped, or they'd register as bogus units."""
    pfx = identity.UNITS_PREFIX.rstrip("/") + "/"
    out: set[str] = set()
    for path in gcs.list_paths(identity.UNITS_PREFIX):
        rest = path[len(pfx):] if path.startswith(pfx) else path
        head, sep, _ = rest.partition("/")
        if sep and head:           # has a sub-path → it's a per-series directory, not a root file
            out.add(head.upper())
    return out


def _vector_manifests_by_sid() -> dict[str, dict]:
    """{SID: {"spatial": [labels], "tables": [labels], "columns": {label: [{name, type}]}}} from
    the per-series manifests pubs/vectors.py writes (`{VECTORS_PREFIX}/<series_id>/_manifest.json`)
    — the AUTHORITATIVE record of which extracted layers are spatial vs non-spatial GeMS companion
    tables, so build_item doesn't have to guess a layer's kind from its name. `columns` holds each
    layer's recorded schema; manifests written before it was recorded have none."""
    pfx = vectors.VECTORS_PREFIX.rstrip("/") + "/"
    out: dict[str, dict] = {}
    for path in gcs.list_paths(vectors.VECTORS_PREFIX):
        if not path.endswith("/_manifest.json"):
            continue
        rest = path[len(pfx):] if path.startswith(pfx) else path
        sid = rest.split("/", 1)[0].upper()
        try:
            doc = json.loads(gcs.get_bytes(path).decode())
            out[sid] = {"spatial": doc.get("spatial") or [], "tables": doc.get("tables") or [],
                        "columns": {lay["label"]: lay["columns"] for lay in doc.get("layers") or []
                                    if lay.get("label") and lay.get("columns")}}
        except Exception as e:  # noqa: BLE001 — a bad manifest just costs that pub its vector assets
            print(f"[pubs] corrupt vector manifest, skipping: {path} ({e})", file=sys.stderr)
            continue
    return out


def _cog_headers(cog_ids: set[str]) -> dict[str, tuple[tuple | None, dict]]:
    """series_id -> (footprint, COG asset fields) from each harvested COG's header.

    The footprint is `(geometry, bbox, "cog")`, the COG's own extent reprojected to EPSG:4326, or None
    if it does not reproject. The asset fields are `sink_stac.cog_asset_fields`.

    The warehouse derives footprints from ITS OWN output — the COG we produced — with NO external
    service. Footprint coverage therefore tracks COG coverage: a pub gets a footprint once we've
    harvested its map. The geometry is the COG's bounding rectangle; the true neatline polygon (from
    the COG's alpha mask) is a later refinement, best done at harvest.

    Reads only the COG header via GDAL /vsicurl (a small range read), parallelised — no full download.
    """
    from rasterio import Env
    from rasterio import open as rio_open
    from rasterio.warp import transform_bounds

    def one(sid: str):
        url = config.public_url(identity.Pub(sid).cog_object)
        try:
            # Without this GDAL lists the CDN "directory" before each open, which is most of the time.
            # The setting is per thread, so it goes here, not around the pool.
            with Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"), rio_open(f"/vsicurl/{url}") as ds:
                # densify so the reprojected 3857→4326 rectangle hugs the curved edges accurately.
                w, s, e, n = transform_bounds(ds.crs, "EPSG:4326", *ds.bounds, densify_pts=21)
                fields = sink_stac.cog_asset_fields(ds)
        except Exception:  # noqa: BLE001 — unreadable COG → no footprint (better than a wrong one)
            return None
        if not all(map(math.isfinite, (w, s, e, n))):
            return sid, (None, fields)
        geom = {"type": "Polygon", "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]}
        return sid, ((geom, [w, s, e, n], "cog"), fields)

    out: dict[str, tuple[tuple | None, dict]] = {}
    with ThreadPoolExecutor(max_workers=32) as ex:
        for r in ex.map(one, sorted(cog_ids)):
            if r:
                out[r[0]] = r[1]
    return out


def build_catalog(limit: int | None = None, series: str | None = None, skip_refresh: bool = False) -> int:
    print(f"[pubs] metadata source: {source.source_name()}")
    pubs = source.read_pubs()
    # Computed over the FULL corpus, before --series/--limit narrow `pubs` below: a same-quad
    # edition link can cross series-type prefixes (an OFR predecessor to a later M map) or fall
    # outside a smoke-test slice, and a filtered run must never blind edition detection to a real
    # predecessor/successor just because this run isn't writing it.
    try:
        fp_rows = editions.footprint_rows()
        layers = editions.layers_by_series(fp_rows)
        tier_by_sid = editions.mosaic_tier_by_series(layers)
        edition_graph = editions.edition_graph(pubs, quad_by_sid=editions.quad_by_series(fp_rows),
                                               tier_by_sid=tier_by_sid)
    except RuntimeError as e:
        print(f"[ingest] WARNING: edition detection skipped — {e}. Items will carry no "
              "version/deprecated/predecessor/successor/latest or mosaic links until the "
              "footprints parquet is built.", file=sys.stderr)
        edition_graph, tier_by_sid = {}, {}
    if series:
        series_upper = series.strip().upper()
        pubs = [p for p in pubs if sink_stac.series_code(p.get("series_id")) == series_upper]
        print(f"[pubs] filtered to series {series_upper}: {len(pubs)} publications")
    if limit:
        pubs = pubs[:limit]
    att: dict[str, list[dict]] = {}
    for a in source.read_attachments():
        att.setdefault((a.get("series_id") or "").strip().upper(), []).append(a)

    cogs = _ids_with_suffix(identity.COG_PREFIX, ".cog.tif")
    thumbs, covers = _image_ids()
    threed_ids = _ids_with_suffix(identity.THREED_PREFIX, f"_{threed.POLY_NAME}")  # converted 3D pubs
    threed_classes = _threed_classes_by_sid()  # authored fence colors (classification:classes)
    overrides_map = _overrides_by_sid()  # hand-authored description/title overrides
    toc = _contents_by_sid()  # Survey Notes "In this issue" sidecars
    units = _unit_ids()
    heads = _cog_headers(cogs)  # footprints + COG asset fields from OUR COGs (no external service)
    mirrored = _mirrored_files()  # source files served from our CDN instead of the publisher's host
    vector_manifests = _vector_manifests_by_sid()  # spatial/table split extracted by pubs/vectors.py
    n_foot = sum(1 for f, _ in heads.values() if f)
    print(f"[pubs] harvested: {len(cogs)} cogs, {len(covers)} covers, {len(toc)} contents, "
          f"{len(units)} unit sets, {n_foot} footprints, {len(threed_ids)} 3D, "
          f"{len(mirrored)} mirrored source files, {len(vector_manifests)} vector manifests")

    def process_pub(p: dict) -> bool:
        sid = (p.get("series_id") or "").strip()
        if not sid:
            return False
        up = sid.upper()
        footprint, cog_fields = heads.get(up, (None, None))
        geom, bbox, fp_source = footprint or (None, None, None)
        manifest = vector_manifests.get(up, {"spatial": [], "tables": []})
        item = sink_stac.build_item(
            p, att.get(up, []), geom=geom, bbox=bbox, fp_source=fp_source,
            has_cog=up in cogs, cog_fields=cog_fields, has_units=up in units, has_thumb=up in thumbs,
            has_cover=up in covers, has_3d=up in threed_ids, classes_3d=threed_classes.get(up),
            vector_layers=manifest["spatial"],
            companion_tables=[{"label": t, "columns": manifest.get("columns", {}).get(t)}
                              for t in manifest["tables"]],
            override=overrides_map.get(up), contents=toc.get(up), mirrored=mirrored,
            edition=edition_graph.get(sid),
            mosaic_tier=tier_by_sid.get(up),
        )
        stac.attach_renders(item)  # ugs-styles GL style -> render extension (graceful if none)
        stac.attach_iso(item)  # ISO 19139 sidecar + `metadata` asset (gov clearinghouses)
        stac.write_item(item)
        return True

    stac.styles.warm()  # prime the styles manifest once before the pool (64 threads share it)
    print("[pubs] writing STAC items in parallel...")
    with ThreadPoolExecutor(max_workers=64) as executor:
        results = list(executor.map(process_pub, pubs))
    n = sum(1 for r in results if r)

    _build_search_corpus()

    if not skip_refresh:
        stac.refresh_catalog()
        print(f"[pubs] wrote {n} items -> {config.public_url(config.STAC_PREFIX + '/catalog.json')}")
    else:
        print(f"[pubs] wrote {n} items (catalog refresh skipped)")
    return n


def _build_search_corpus() -> None:
    """Aggregate the per-issue full-text sidecars (pubs/search/{SID}.json, written by the thumbs job)
    into ONE corpus the viewer loads + indexes client-side: pubs/search/corpus.json, a flat list of
    {id, sid, volume, issue, title, page, text} per article. Best-effort — skipped on any read error."""
    prefix = identity.PUB_SEARCH_PREFIX
    paths = [p for p in gcs.list_paths(prefix) if p.endswith(".json") and not p.endswith("/corpus.json")]
    if not paths:
        print("[pubs] no search sidecars — corpus not built")
        return

    def load(path: str) -> list[dict]:
        try:
            doc = json.loads(gcs.get_bytes(path).decode())
        except Exception:  # noqa: BLE001
            return []
        sid, vol, issue, pdf = doc.get("series_id"), doc.get("volume"), doc.get("title"), doc.get("pdf")
        # id is index-based (not page-based): two TOC entries can share a page, and the search index
        # requires unique ids.
        # Classify each article from its (topical) title + the start of its body — so Survey Notes
        # articles get a real topic instead of the whole issue collapsing to one.
        return [{"id": f"{sid}#{i}", "sid": sid, "volume": vol, "issue": issue, "pdf": pdf,
                 "title": a.get("title"), "page": a.get("page"), "text": a.get("text") or "",
                 "topic": topic.LABELS[topic.classify(a.get("title"), (a.get("text") or "")[:300])]}
                for i, a in enumerate(doc.get("articles") or [])]

    corpus: list[dict] = []
    with ThreadPoolExecutor(max_workers=16) as ex:
        for arts in ex.map(load, paths):
            corpus.extend(arts)
    gcs.put_bytes(json.dumps(corpus).encode(), f"{prefix}/corpus.json",
                  content_type="application/json", cache_control=gcs.CACHE_MUTABLE, compress=True)
    print(f"[pubs] search corpus: {len(corpus)} articles from {len(paths)} issues "
          f"-> {config.public_url(prefix + '/corpus.json')}")


def list_series() -> int:
    print(f"[pubs] metadata source: {source.source_name()}")
    pubs = source.read_pubs()
    counts: dict[str, int] = {}
    for p in pubs:
        code = sink_stac.series_code(p.get("series_id"))
        counts[code] = counts.get(code, 0) + 1
    print("Discovered series codes:")
    for code, count in sorted(counts.items(), key=lambda x: (-x[1], x[0])):
        print(f"  {code:<10} : {count} publications")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the ugs-publications STAC collection")
    ap.add_argument("--limit", type=int, default=None, help="only the first N pubs (smoke test)")
    ap.add_argument("--series", help="only process publications of this data series code (e.g. DS, OFR, M, etc.)")
    ap.add_argument("--skip-refresh", action="store_true", help="skip the final STAC catalog refresh")
    ap.add_argument("--list-series", action="store_true", help="list all unique series codes and counts, then exit")
    args = ap.parse_args()

    if args.list_series:
        return list_series()

    build_catalog(limit=args.limit, series=args.series, skip_refresh=args.skip_refresh)
    return 0


if __name__ == "__main__":
    sys.exit(main())
