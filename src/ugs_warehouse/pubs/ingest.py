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


def build_catalog(limit: int | None = None) -> int:
    print(f"[pubs] metadata source: {source.source_name()}")
    pubs = source.read_pubs()
    if limit:
        pubs = pubs[:limit]
    att: dict[str, list[dict]] = {}
    for a in source.read_attachments():
        att.setdefault((a.get("series_id") or "").strip().upper(), []).append(a)

    cogs = _ids_with_suffix(identity.COG_PREFIX, ".cog.tif")
    thumbs = _ids_with_suffix(identity.COG_PREFIX, ".thumb.png")
    units = _unit_ids()
    foot = _footprint_geoms()
    print(f"[pubs] harvested: {len(cogs)} cogs, {len(units)} unit sets, {len(foot)} footprints")

    def process_pub(p: dict) -> bool:
        sid = (p.get("series_id") or "").strip()
        if not sid:
            return False
        up = sid.upper()
        geom, bbox, fp_source = foot.get(up, (None, None, None))
        item = sink_stac.build_item(
            p, att.get(up, []), geom=geom, bbox=bbox, fp_source=fp_source,
            has_cog=up in cogs, has_units=up in units, has_thumb=up in thumbs,
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

    stac.refresh_catalog()
    print(f"[pubs] wrote {n} items -> {config.public_url(config.STAC_PREFIX + '/catalog.json')}")
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the ugs-publications STAC collection")
    ap.add_argument("--limit", type=int, default=None, help="only the first N pubs (smoke test)")
    args = ap.parse_args()
    build_catalog(limit=args.limit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
