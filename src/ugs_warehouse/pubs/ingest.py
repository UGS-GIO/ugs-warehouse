"""Write each publication as a STAC Collection with its Items, from pub metadata + harvested artifacts.

Every pub -> a Collection at `<group>/<SERIES>/<SID>/collection.json` holding its documents, with
its plate COG, units, extracted layers/tables and 3D fence as Items under it (a lone data file
stays on the Collection). Harvested-artifact presence is derived by listing GCS once (not a HEAD
per pub). Then `core.stac.refresh_catalog(strict=True)` writes the series and group catalogs and
the root, so the pubs land in the SAME catalog as the vector serving topics; a series switches to
the collection layout once every publication in it has been written.

    python -m ugs_warehouse.pubs.ingest            # all pubs
    python -m ugs_warehouse.pubs.ingest --limit 50 # first N (smoke)
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from ..core import config, gcs, stac
from . import editions, identity, relations, sink_stac, source, threed, topic, vectors


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
    once (operators backfill only a handful) so build_publication doesn't do a GCS read per pub."""
    out: dict[str, dict] = {}
    for path in gcs.list_paths(config.OVERRIDES_PREFIX):
        if not path.endswith(".json"):
            continue
        sid = path.rsplit("/", 1)[-1][: -len(".json")].upper()
        try:
            doc = json.loads(gcs.get_bytes(path).decode())
        except Exception as e:
            raise ValueError(f"unreadable override {path}: {type(e).__name__}: {e}") from e
        if not isinstance(doc, dict):
            raise ValueError(f"override {path} is not a JSON object")
        out[sid] = doc
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
    set membership is everything `build_publication` needs to decide which assets serve from our CDN.
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


def _source_file(layer: dict) -> dict | None:
    """A manifest layer's `source_file` {key?, title, path}, None when it records none (extracted
    before attachments were tracked). A malformed one raises, so its publication fails instead of
    publishing an Item that names no file."""
    file = layer.get("source_file")
    if file is None:
        return None
    if not (isinstance(file, dict) and all(isinstance(file.get(k), str) and file[k]
                                           for k in ("title", "path"))
            and (file.get("key") is None or isinstance(file["key"], str))):
        raise ValueError(f"layer {layer.get('label')!r}: malformed source_file {file!r}")
    return file


def _vector_manifest(sid: str, doc: dict) -> dict:
    """One series' manifest reduced to what the items are built from: {"spatial": [labels],
    "tables": [labels], "empty": [labels], "columns": {label: [{name, type}]}, "relationships":
    [...], "layers": {label: {rows, bbox, geometry_types, proj_code, proj_wkt2, columns,
    pmtiles_layer, style, source, source_file}}}. `columns` holds each layer's recorded schema and `layers` its row
    count, bbox and CRS; manifests written before they were recorded have none (empty maps / None
    values); `source` comes from the label when the manifest records none. A layer's kind is not
    read: build_publication derives it from the relationships it publishes."""
    layer_docs = [lay for lay in doc.get("layers") or [] if lay.get("label")]
    return {"spatial": doc.get("spatial") or [], "tables": doc.get("tables") or [],
            "empty": doc.get("empty") or [],
            "columns": {lay["label"]: lay["columns"] for lay in layer_docs if lay.get("columns")},
            "relationships": _relationships(doc.get("relationships") or []),
            "layers": {lay["label"]: {"rows": lay.get("rows"), "bbox": lay.get("bbox"),
                                      "geometry_types": lay.get("geometry_types") or [],
                                      "proj_code": lay.get("proj_code"),
                                      "proj_wkt2": lay.get("proj_wkt2"),
                                      "columns": lay.get("columns"),
                                      "pmtiles_layer": (lay.get("pmtiles_layer")
                                                        if lay.get("pmtiles") else None),
                                      "style": lay.get("style") if lay.get("pmtiles") else None,
                                      "source": lay.get("source")
                                      or relations.source_of_label(lay["label"], sid),
                                      "source_file": _source_file(lay)}
                       for lay in layer_docs},
            **({"geopackage": doc["geopackage"]} if (doc.get("geopackage") or {}).get("bytes") else {}),
            **({"unit_polys": doc["unit_polys"]} if (doc.get("unit_polys") or {}).get("pmtiles") else {})}


def _reduce_vector_manifest(sid: str, doc: dict) -> tuple[dict | None, str | None, vectors.Needs | None]:
    """(the reduced manifest, why it is malformed, what the self-heal would do) for one series, in
    the thread that read it, so the full document is dropped as soon as this returns."""
    needs = vectors.needs_of(doc) if vectors.is_clean(doc) else None
    try:
        return _vector_manifest(sid.upper(), doc), None, needs
    except Exception as e:  # noqa: BLE001 (recorded per series; the caller fails that pub)
        return None, f"{type(e).__name__}: {e}", needs


def _read_vector_manifests(paths: list[str] | None = None
                           ) -> tuple[dict[str, dict], dict[str, str], dict[str, vectors.Needs]]:
    """Read the per-series manifests pubs/vectors.py writes (`{VECTORS_PREFIX}/<series_id>/
    _manifest.json`), once, on `vectors.scan_manifests`'s threads. They are the AUTHORITATIVE
    record of which extracted layers are spatial vs non-spatial GeMS companion tables, so
    build_publication doesn't have to guess a layer's kind from its name. Returns ({SID:
    `_vector_manifest`}, {SID: cause} for each manifest that exists but could not be read or
    parsed, {series id: Needs} for each clean one). A series with no manifest appears in none of
    them. `paths` narrows the read to those manifests."""
    out: dict[str, dict] = {}
    failed: dict[str, str] = {}
    needs: dict[str, vectors.Needs] = {}
    for sid, scanned in vectors.scan_manifests(_reduce_vector_manifest, paths).items():
        if scanned.error:
            cause = scanned.error
        else:
            reduced, cause, need = scanned.value
            if need is not None:
                needs[sid] = need
            if reduced is not None:
                out[sid.upper()] = reduced
        if cause:
            failed[sid.upper()] = f"unreadable vector manifest {scanned.path}: {cause}"
            print(f"[pubs] {failed[sid.upper()]}", file=sys.stderr)
    return out, failed, needs


def _relationships(rels: list) -> list[dict]:
    """A manifest's relationships, each checked for the fields the items are built from: ends,
    matching column lists, `detected_by` and, when inferred, an integer `confidence`. A malformed
    one raises, so its publication fails instead of publishing a broken or mislabelled join."""
    for r in rels:
        ok = (isinstance(r, dict) and all(isinstance(r.get(k), str) and r[k]
                                          for k in ("origin", "destination"))
              and all(isinstance(r.get(k), list) and r[k] and all(isinstance(c, str) for c in r[k])
                      for k in ("origin_cols", "destination_cols"))
              and len(r["origin_cols"]) == len(r["destination_cols"])
              and r.get("detected_by") in ("declared", "inferred")
              and (r["detected_by"] == "declared" or (isinstance(r.get("confidence"), int)
                                                      and not isinstance(r["confidence"], bool))))
        if not ok:
            raise ValueError(f"malformed relationship {r!r}")
    return rels


def _published_items(pubs: list[dict]) -> dict[str, dict[str, list[str]]]:
    """{collection path: {item dir: [object paths]}} for every publication already in the catalog,
    from one listing per group (not one per publication) taken before the pool writes anything.
    Files directly in a publication directory (collection.json, README.md, AGENTS.md) are not
    item directories and are left out."""
    groups = sorted({sink_stac.pub_collection_path(p).split("/")[0]
                     for p in pubs if (p.get("series_id") or "").strip()})
    out: dict[str, dict[str, list[str]]] = {}
    root = f"{config.STAC_PREFIX}/"
    for group in groups:
        for path in gcs.list_paths(f"{root}{group}/"):
            layout = stac.pub_layout_of(path[len(root):])
            if layout and layout[2] and "/" in layout[3]:  # <group>/<SERIES>/<SID>/<item dir>/<file>
                _, series, sid, rest = layout
                out.setdefault(f"{group}/{series}/{sid}", {}).setdefault(
                    rest.split("/")[0], []).append(path)
    return out


def _stale_items(coll_path: str, keep: set[str], published: dict[str, dict[str, list[str]]]
                 ) -> dict[str, list[str]]:
    """{item path: its objects} for the item directories under a publication that its current build
    no longer produces. A publication that gained or lost data files (a plate moving to the
    Collection, a layer dropped) would otherwise keep serving its old Items."""
    return {f"{coll_path}/{head}": paths for head, paths in published.get(coll_path, {}).items()
            if head not in keep}


def _delete_stale_items(stale: dict[str, list[str]]) -> None:
    """Delete the objects of every stale item in one batched call, raising if any delete fails."""
    gcs.delete_many([p for paths in stale.values() for p in paths])
    for item, paths in sorted(stale.items()):
        print(f"[pubs] removed stale item {item} ({len(paths)} objects)", file=sys.stderr)


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


FILE_META_PREFIXES = (identity.COG_PREFIX, identity.UNITS_PREFIX, identity.THREED_PREFIX,
                      identity.PUB_THUMB_PREFIX, identity.PUB_FILES_PREFIX, vectors.VECTORS_PREFIX)


def _file_meta_index() -> dict[str, gcs.FileMeta]:
    """{object_path: FileMeta} for every prefix a pub asset can point into: one listing each."""
    index: dict[str, gcs.FileMeta] = {}
    with ThreadPoolExecutor(max_workers=len(FILE_META_PREFIXES)) as ex:
        for part in ex.map(lambda p: gcs.list_file_meta(p.rstrip("/") + "/"), FILE_META_PREFIXES):
            index.update(part)
    return index


# The self-heal stops starting series this long after build_catalog starts, so the rest of the
# ingest still fits the job's 1800 s task timeout.
HEAL_BUDGET_S = 600


def _self_heal(pubs: list[dict], needs: dict[str, vectors.Needs], deadline: float) -> vectors.Heal:
    """`vectors.self_heal` over the clean vector manifests and units files of `pubs`: the
    relationships extracted layers are missing, and units files older than GeoParquet 1.1, before
    the items are built. PMTiles are not built here: tiling reads whole layers, which the vectors
    job is sized for and this one (4Gi) is not, so a layer without tiles is tiled by the next
    vectors run."""
    wanted = {(p.get("series_id") or "").strip().upper() for p in pubs}
    mine = {sid: n for sid, n in needs.items() if sid.upper() in wanted}
    heal = vectors.self_heal(mine, deadline=deadline, units=vectors.units_objects(wanted), tiles=False)
    print(f"[pubs] {heal.summary(next_step='refresh')}")
    return heal


def build_catalog(limit: int | None = None, series: str | None = None, skip_refresh: bool = False) -> int:
    """Write the publications' Collections and Items. First fill in the PMTiles and relationships
    already-extracted pubs are missing (`_self_heal`, within HEAL_BUDGET_S), so the items carry them
    in the same run; a heal failure fails the run like a publication's, and work left by the budget
    waits for the next refresh."""
    started = time.monotonic()
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
    _refuse_shared_collection_paths(pubs)
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
    vector_manifests, manifest_failures, needs = _read_vector_manifests()  # spatial/table split extracted by pubs/vectors.py
    healed = _self_heal(pubs, needs, started + HEAL_BUDGET_S)
    del needs
    if healed.touched:  # build the items from the healed manifests, re-reading only those
        fresh, fresh_failed, _ = _read_vector_manifests(
            [f"{vectors.VECTORS_PREFIX}/{sid}/_manifest.json" for sid in sorted(healed.touched)])
        for sid in healed.touched:
            vector_manifests.pop(sid.upper(), None)
            manifest_failures.pop(sid.upper(), None)
        vector_manifests.update(fresh)
        manifest_failures.update(fresh_failed)
    file_meta = _file_meta_index()  # file:size/checksum for the assets in our bucket
    n_foot = sum(1 for f, _ in heads.values() if f)
    print(f"[pubs] harvested: {len(cogs)} cogs, {len(covers)} covers, {len(toc)} contents, "
          f"{len(units)} unit sets, {n_foot} footprints, {len(threed_ids)} 3D, "
          f"{len(mirrored)} mirrored source files, {len(vector_manifests)} vector manifests")

    errors: list[tuple[str, str]] = [(sid, f"self-heal: {len(errs)} failed this run: {'; '.join(errs)}")
                                     for sid, errs in sorted(healed.failed_by_series.items())]

    def process_pub(p: dict) -> bool:
        """Write one publication; a failure is collected so the other publications still land."""
        try:
            return _write_pub(p)
        except Exception as e:  # noqa: BLE001 (collected and raised after the pool)
            errors.append(((p.get("series_id") or "").strip(), f"{type(e).__name__}: {e}"))
            return False

    def _write_pub(p: dict) -> bool:
        sid = (p.get("series_id") or "").strip()
        if not sid:
            return False
        up = sid.upper()
        if up in manifest_failures:  # writing now would prune the layer and table items it lists
            raise RuntimeError(manifest_failures[up])
        footprint, cog_fields = heads.get(up, (None, None))
        geom, bbox, fp_source = footprint or (None, None, None)
        manifest = vector_manifests.get(up, {"spatial": [], "tables": [], "layers": {}})
        coll, items = sink_stac.build_publication(
            p, att.get(up, []), geom=geom, bbox=bbox, fp_source=fp_source,
            has_cog=up in cogs, cog_fields=cog_fields, has_units=up in units, has_thumb=up in thumbs,
            has_cover=up in covers, has_3d=up in threed_ids, classes_3d=threed_classes.get(up),
            vector_layers=manifest["spatial"],
            companion_tables=[{"label": t, "columns": manifest.get("columns", {}).get(t)}
                              for t in manifest["tables"]],
            layers=manifest.get("layers", {}),
            relationships=manifest.get("relationships", []),
            empty_layers=manifest.get("empty", []),
            override=overrides_map.get(up), contents=toc.get(up), mirrored=mirrored,
            edition=edition_graph.get(sid),
            mosaic_tier=tier_by_sid.get(up),
            geopackage=manifest.get("geopackage"),
            unit_polys=manifest.get("unit_polys"),
        )
        for it in items:
            stac.attach_renders(it)  # ugs-styles GL style, else the layer's generated one
            stac.stamp_file_meta(it, file_meta)
            stac.write_item(it)
        stac.attach_renders(coll)  # a single-file publication's layer rides the Collection
        stac.stamp_file_meta(coll, file_meta)
        stac.write_collection(coll, children=len(items))
        stale.update(_stale_items(coll["_collection_path"], {it["id"] for it in items}, published))
        return True

    stac.styles.warm()  # prime the styles manifest once before the pool (64 threads share it)
    published = _published_items(pubs)
    stale: dict[str, list[str]] = {}  # item path -> objects, from the publications that wrote cleanly
    print("[pubs] writing STAC items in parallel...")
    with ThreadPoolExecutor(max_workers=64) as executor:
        results = list(executor.map(process_pub, pubs))
    n = sum(1 for r in results if r)
    prune_error: Exception | None = None
    try:
        _delete_stale_items(stale)
    except Exception as e:  # noqa: BLE001 (raised below together with the failed publications)
        prune_error = e
        print(f"[pubs] stale items not removed: {type(e).__name__}: {e}", file=sys.stderr)

    _build_search_corpus()

    # Printed before the refresh, so a refresh failure cannot hide the per-publication causes.
    for sid, msg in errors:
        print(f"[pubs] FAILED {sid}: {msg}", file=sys.stderr)
    refresh_error: Exception | None = None
    if not skip_refresh:
        try:
            stac.refresh_catalog(strict=True)
        except Exception as e:  # noqa: BLE001 (raised below together with the failed publications)
            refresh_error = e
        print(f"[pubs] wrote {n} publications -> {config.public_url(config.STAC_PREFIX + '/catalog.json')}")
    else:
        print(f"[pubs] wrote {n} publications (catalog refresh skipped)")
    if errors or refresh_error or prune_error:
        # A pub whose heal and write both failed has two errors but counts once.
        failed_sids = sorted({sid for sid, _ in errors})
        failed = [f"{len(failed_sids)} publication(s) failed: {failed_sids}"] if errors else []
        pruned = [f"stale items: {type(prune_error).__name__}: {prune_error}"] if prune_error else []
        refresh = [f"catalog refresh: {type(refresh_error).__name__}: {refresh_error}"] if refresh_error else []
        raise RuntimeError("; ".join(failed + pruned + refresh)) from (refresh_error or prune_error)
    return n


def _refuse_shared_collection_paths(pubs: list[dict]) -> None:
    """Raise when two publications map to one collection directory (`A B` and `A-B` both become
    `A-B`), since their parallel writes would overwrite each other."""
    by_path: dict[str, list[str]] = {}
    for p in pubs:
        sid = (p.get("series_id") or "").strip()
        if sid:
            by_path.setdefault(sink_stac.pub_collection_path(p), []).append(sid)
    shared = {path: sids for path, sids in sorted(by_path.items()) if len(sids) > 1}
    if shared:
        raise RuntimeError(f"{len(shared)} publication collection path(s) claimed by more than one "
                           f"publication: {'; '.join(f'{path} <- {sids}' for path, sids in shared.items())}")


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
