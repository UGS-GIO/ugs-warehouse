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
from concurrent.futures import ThreadPoolExecutor
import json
import sys

from ..core import config, gcs, stac
from . import identity, sink_stac, source


def _ids_with_suffix(prefix: str, suffix: str) -> set[str]:
    out: set[str] = set()
    for path in gcs.list_paths(prefix):
        name = path.rsplit("/", 1)[-1]
        if name.endswith(suffix):
            out.add(name[: -len(suffix)].upper())
    return out


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


def _unit_ids() -> set[str]:
    pfx = identity.UNITS_PREFIX.rstrip("/") + "/"
    out: set[str] = set()
    for path in gcs.list_paths(identity.UNITS_PREFIX):
        rest = path[len(pfx):] if path.startswith(pfx) else path
        seg = rest.split("/", 1)[0]
        if seg:
            out.add(seg.upper())
    return out


def _footprint_geoms() -> dict[str, tuple]:
    """series_id -> (geometry, bbox, source). Filled by Phase 2 (pubs/footprints.py);
    until footprints land, items get null geometry."""
    try:
        from . import footprints
        return footprints.geoms()
    except (ImportError, AttributeError, FileNotFoundError):
        return {}


def build_catalog(limit: int | None = None, series: str | None = None, skip_refresh: bool = False) -> int:
    print(f"[pubs] metadata source: {source.source_name()}")
    pubs = source.read_pubs()
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
    thumbs = _ids_with_suffix(identity.COG_PREFIX, ".thumb.png")
    covers = _ids_with_suffix(identity.PUB_THUMB_PREFIX, ".png")  # PDF first-page covers
    toc = _contents_by_sid()  # Survey Notes "In this issue" sidecars
    units = _unit_ids()
    foot = _footprint_geoms()
    print(f"[pubs] harvested: {len(cogs)} cogs, {len(covers)} covers, {len(toc)} contents, "
          f"{len(units)} unit sets, {len(foot)} footprints")

    def process_pub(p: dict) -> bool:
        sid = (p.get("series_id") or "").strip()
        if not sid:
            return False
        up = sid.upper()
        geom, bbox, fp_source = foot.get(up, (None, None, None))
        item = sink_stac.build_item(
            p, att.get(up, []), geom=geom, bbox=bbox, fp_source=fp_source,
            has_cog=up in cogs, has_units=up in units, has_thumb=up in thumbs,
            has_cover=up in covers, contents=toc.get(up),
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

    if not skip_refresh:
        stac.refresh_catalog()
        print(f"[pubs] wrote {n} items -> {config.public_url(config.STAC_PREFIX + '/catalog.json')}")
    else:
        print(f"[pubs] wrote {n} items (catalog refresh skipped)")
    return n


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
