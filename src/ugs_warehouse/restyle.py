"""Rebind ugs-styles → STAC renders, without a full reingest.

A style change (new palette, tweaked spec, published render) doesn't change the data — only how
it's drawn. This re-fetches the ugs-styles manifest and re-runs `attach_renders` over the STAC
items already in GCS, rewriting just the item.json files whose `renders` changed. No DB read, no
transform, no PMTiles — seconds, not minutes. Meant to be triggered by the ugs-styles publish
(after the CDN rsync) so a style edit reaches the viewer hands-free.

    python -m ugs_warehouse.restyle                    # rebind ugs-serving-topics (default scope)
    python -m ugs_warehouse.restyle --collection all   # every collection, incl. pubs
    python -m ugs_warehouse.restyle --refresh          # also rebuild collection.json/items.json
    python -m ugs_warehouse.restyle --dry-run          # report what would change, write nothing
    python -m ugs_warehouse.restyle --workers 16       # cap parallelism (default 32)

Renders live on the item.json (what the viewer reads for a layer's style_url), so a refresh of
the items.json index isn't required for styling to take effect — it's offered only to keep the
index's asset summaries exact (the `style` asset chip).
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor

from .core import config, gcs, stac, styles


def _clear_renders(item: dict) -> bool:
    """Strip any existing render binding so a re-attach is authoritative (a removed style must
    drop out, not linger). Returns True if the item had a render binding."""
    props = item.get("properties") or {}
    had = "ugs:renders" in props or "renders" in props  # "renders" = pre-rename items, also strip
    props.pop("ugs:renders", None)
    props.pop("renders", None)
    # Drop any legacy STAC render-extension declaration (we no longer use it — see attach_renders).
    legacy_render = "stac-extensions.github.io/render/"
    exts = [e for e in (item.get("stac_extensions") or []) if legacy_render not in e]
    if exts:
        item["stac_extensions"] = exts
    else:
        item.pop("stac_extensions", None)
    # attach_renders adds the default render's GL fragment as a `style`-keyed asset.
    (item.get("assets") or {}).pop("style", None)
    return had


def _scoped_groups(collection: str) -> dict[str, list[str]]:
    """{collection_path: [item_id]} for the requested scope.

    Lists only the collection's sub-prefix (not the whole catalog) so a 25-item
    serving-topics rebind doesn't enumerate ~7k pub items first. `all` lists everything.
    """
    list_prefix = (
        config.STAC_PREFIX if collection == "all" else f"{config.STAC_PREFIX}/{collection}/"
    )
    groups = stac._group_items(gcs.list_paths(list_prefix))
    if collection != "all":
        groups = {
            cp: iids
            for cp, iids in groups.items()
            if cp == collection or cp.startswith(collection + "/")
        }
    return groups


def report(*, collection: str = "ugs-serving-topics") -> int:
    """Diagnose binding: for every item in scope say matched / id-miss / asset-miss, and list
    manifest styles that matched no item in scope. Read-only — writes nothing. Use this when
    'some styles refresh but not all': it shows exactly which item ids or asset keys don't line up.
    """
    styles.warm()
    manifest = styles._manifest()
    man_ids = {styles._entry_key(e) for e in manifest}
    man_assets: dict[str, set[str]] = {}
    for e in manifest:
        default = ["cog"] if str(e.get("kind") or "vector") == "raster" else ["pmtiles"]
        man_assets.setdefault(styles._entry_key(e), set()).update(e.get("assets") or default)
    print(f"[report] manifest: {len(manifest)} entries, {len(man_ids)} distinct item ids")

    groups = _scoped_groups(collection)
    matched, asset_miss, no_style, scope_ids = [], [], [], set()
    for coll_path, iids in groups.items():
        for iid in sorted(iids):
            scope_ids.add(iid)
            try:
                item = json.loads(gcs.get_bytes(stac.item_object_path(coll_path, iid)).decode())
            except Exception:  # noqa: BLE001
                continue
            keys = set((item.get("assets") or {}).keys())
            renders, _ = styles.renders_for(iid, keys)
            if renders:
                matched.append(iid)
            elif iid in man_ids:
                asset_miss.append((iid, sorted(man_assets.get(iid, set())), sorted(keys)))
            else:
                no_style.append(iid)

    orphans = sorted(man_ids - scope_ids)
    print(f"[report] in scope '{collection}': {len(scope_ids)} items — "
          f"{len(matched)} styled, {len(asset_miss)} asset-miss, {len(no_style)} no manifest entry")
    if asset_miss:
        print("  ASSET-MISS (id matches a style, but the targeted asset key isn't on the item):")
        for iid, want, have in asset_miss:
            print(f"    {iid}: style wants assets {want}, item has asset keys {have}")
    if orphans:
        print(f"  ORPHAN styles ({len(orphans)}) — manifest item ids with no matching item in scope "
              "(wrong/renamed id, or item lives in another collection):")
        for iid in orphans:
            print(f"    {iid}")
    if no_style:
        print(f"  no manifest entry ({len(no_style)}): {', '.join(no_style)}")
    return len(asset_miss) + len(orphans)


def restyle(*, collection: str = "ugs-serving-topics", refresh: bool = False,
            dry_run: bool = False, workers: int = 32) -> int:
    groups = _scoped_groups(collection)
    n_entries = styles.warm()
    print(f"[restyle] styles manifest: {n_entries} entries")

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
        now = "ugs:renders" in (item.get("properties") or {})
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
    ap.add_argument("--report", action="store_true",
                    help="diagnose binding (matched / id-miss / asset-miss + orphan styles); writes nothing")
    ap.add_argument("--workers", type=int, default=32,
                    help="number of parallel threads for GCS operations (default: 32)")
    args = ap.parse_args()
    if args.report:
        report(collection=args.collection)
        return 0
    restyle(collection=args.collection, refresh=args.refresh, dry_run=args.dry_run, workers=args.workers)
    return 0


if __name__ == "__main__":
    sys.exit(main())
