"""Rebind ugs-styles → STAC renders, without a full reingest.

A style change (new palette, tweaked spec, published render) doesn't change the data — only how
it's drawn. This re-fetches the ugs-styles manifest and re-runs `attach_renders` over the STAC
items already in GCS, rewriting just the item.json files whose `renders` changed. No DB read, no
transform, no PMTiles — seconds, not minutes. Meant to be triggered by the ugs-styles publish
(after the CDN rsync) so a style edit reaches the viewer hands-free.

    python -m ugs_warehouse.restyle              # rebind all items
    python -m ugs_warehouse.restyle --refresh    # also rebuild collection.json/items.json
    python -m ugs_warehouse.restyle --dry-run     # report what would change, write nothing

Renders live on the item.json (what the viewer reads for a layer's style_url), so a refresh of
the items.json index isn't required for styling to take effect — it's offered only to keep the
index's asset summaries exact (the `style` asset chip).
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import sys

from .core import config, gcs, stac
from .core import styles


def _clear_renders(item: dict) -> bool:
    """Strip any existing render binding so a re-attach is authoritative (a removed style must
    drop out, not linger). Returns True if the item had a render binding."""
    props = item.get("properties") or {}
    had = "renders" in props
    props.pop("renders", None)
    exts = [e for e in (item.get("stac_extensions") or []) if e != styles.RENDER_EXT]
    if exts:
        item["stac_extensions"] = exts
    else:
        item.pop("stac_extensions", None)
    # attach_renders adds the default render's GL fragment as a `style`-keyed asset.
    (item.get("assets") or {}).pop("style", None)
    return had


def restyle(*, collection: str = "ugs-serving-topics", refresh: bool = False,
            dry_run: bool = False, workers: int = 32) -> int:
    groups = stac._group_items(gcs.list_paths(config.STAC_PREFIX))
    n_entries = styles.warm()
    print(f"[restyle] styles manifest: {n_entries} entries")

    if collection != "all":
        groups = {
            cp: iids
            for cp, iids in groups.items()
            if cp == collection or cp.startswith(collection + "/")
        }

    tasks = []
    for coll_path, item_ids in groups.items():
        for iid in sorted(item_ids):
            tasks.append((coll_path, iid))

    def process_task(task: tuple[str, str]) -> bool:
        coll_path, iid = task
        obj = stac.item_object_path(coll_path, iid)
        try:
            item = json.loads(gcs.get_bytes(obj).decode())
        except Exception:  # noqa: BLE001 — a missing/corrupt item shouldn't sink the rebind
            return False
        had = _clear_renders(item)
        stac.attach_renders(item)  # re-match against the fresh manifest
        now = "renders" in (item.get("properties") or {})
        if not had and not now:
            return False  # never styled (e.g. a pub COG plate) — leave it untouched
        if dry_run:
            print(f"  {'~' if had else '+'} {coll_path}/{iid}"
                  + ("" if now else "  (style removed)"))
            return True
        item["_collection_path"] = coll_path  # preserve nested layout on write
        stac.write_item(item)
        print(f"  {'~' if had else '+'} {config.public_url(obj)}")
        return True

    with ThreadPoolExecutor(max_workers=workers) as executor:
        results = list(executor.map(process_task, tasks))
    changed = sum(1 for r in results if r)

    if refresh and not dry_run:
        stac.refresh_catalog()
    print(f"[restyle] {'would rebind' if dry_run else 'rebound'} {changed} item(s)"
          + ("" if refresh or dry_run else " (run --refresh to refresh items.json asset summaries)"))
    return changed


def main() -> int:
    ap = argparse.ArgumentParser(description="Rebind ugs-styles renders into STAC (no reingest)")
    ap.add_argument("--collection", default="ugs-serving-topics",
                    help="limit restyle to a specific collection (default: ugs-serving-topics; use 'all' for everything)")
    ap.add_argument("--refresh", action="store_true",
                    help="also rebuild collection.json + items.json (keeps index asset summaries exact)")
    ap.add_argument("--dry-run", action="store_true", help="report changes, write nothing")
    ap.add_argument("--workers", type=int, default=32,
                    help="number of parallel threads for GCS operations (default: 32)")
    args = ap.parse_args()
    restyle(collection=args.collection, refresh=args.refresh, dry_run=args.dry_run, workers=args.workers)
    return 0


if __name__ == "__main__":
    sys.exit(main())
