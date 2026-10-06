"""Backfill `file:size` / `file:checksum` onto published STAC items without a reingest.

Hashes our-bucket assets that have no stored checksum, stores it on the object, stamps the items
in place, and refreshes the catalog. Re-runnable; `--max-bytes` defers large COGs to a GCP run.

    python -m scripts.backfill_file_meta            # dry run: counts and bytes
    python -m scripts.backfill_file_meta --apply
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor

from ugs_warehouse.core import config, gcs, stac


def _read_items() -> tuple[dict[str, dict], int]:
    """({object_path: item} for every item the catalog refresh indexes, unreadable count)."""
    groups = stac._group_items(gcs.list_paths(config.STAC_PREFIX))
    paths = [stac.item_object_path(c, i) for c, ids in groups.items() for i in ids]
    def read(path: str) -> dict | None:
        try:
            return json.loads(gcs.get_bytes(path))
        except FileNotFoundError:  # deleted between the listing and the read
            return {}
        except Exception as e:  # noqa: BLE001 — skip it, finish the rest, exit 1
            print(f"  unreadable {path}: {type(e).__name__}: {e}", file=sys.stderr)
            return None
    with ThreadPoolExecutor(max_workers=64) as ex:
        docs = dict(zip(paths, ex.map(read, paths)))
    return {p: d for p, d in docs.items() if d}, sum(d is None for d in docs.values())


def _targets(items: dict[str, dict]) -> set[str]:
    return {p for it in items.values() for a in (it.get("assets") or {}).values()
            if "file:checksum" not in a and (p := stac.object_path_of(a.get("href", "")))}


def _index(targets: set[str]) -> dict[str, gcs.FileMeta]:
    """One listing per two-segment prefix the targets live under (`geolmap/cogs/`, …)."""
    prefixes = sorted({"/".join(parts[:2]) + "/" for p in targets if (parts := p.split("/")[:-1])})
    index: dict[str, gcs.FileMeta] = {}
    with ThreadPoolExecutor(max_workers=16) as ex:
        for part in ex.map(gcs.list_file_meta, prefixes):
            index.update(part)
    return index


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    ap.add_argument("--max-bytes", type=int, default=None, help="skip hashing objects larger than this")
    ap.add_argument("--skip-items", action="store_true", help="hash objects only; leave items alone")
    ap.add_argument("--limit", type=int, default=None, help="hash at most this many objects")
    ap.add_argument("--workers", type=int, default=16)
    args = ap.parse_args()

    items, unreadable = _read_items()
    targets = _targets(items)
    index = _index(targets)
    missing = sorted(t for t in targets if t not in index)  # gone, or gzipped with no metadata
    todo = [t for t in targets if t in index and not index[t].checksum]
    big = [t for t in todo if args.max_bytes is not None and index[t].size > args.max_bytes]
    todo = sorted(t for t in todo if t not in big)[:args.limit]

    def gb(paths: list[str]) -> float:
        return sum(index[p].size for p in paths) / 1e9
    print(f"{len(items)} items ({unreadable} unreadable), {len(targets)} assets in our bucket without a checksum; "
          f"hash {len(todo)} objects ({gb(todo):.2f} GB), skip {len(big)} over --max-bytes "
          f"({gb(big):.2f} GB), {len(missing)} not found")
    for p in missing[:10]:
        print(f"  not found: {p}")

    failed = 0
    if args.apply:
        def hash_one(path: str) -> gcs.FileMeta | None:
            try:
                meta = gcs.hash_object(path)
                gcs.set_file_meta(path, meta)
                return meta
            except Exception as e:  # noqa: BLE001 — one bad object must not stop the rest; rc says so
                print(f"  failed {path}: {type(e).__name__}: {e}", file=sys.stderr)
                return None
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            for n, (path, meta) in enumerate(zip(todo, ex.map(hash_one, todo)), 1):
                if meta is None:
                    failed += 1
                else:
                    index[path] = meta
                if n % 500 == 0:
                    print(f"  hashed {n}/{len(todo)}")
        print(f"hashed {len(todo) - failed}, failed {failed}")

    rc = 1 if failed or unreadable else 0
    if args.skip_items:
        return rc
    changed = {p: it for p, it in items.items() if stac.stamp_file_meta(it, index)}
    print(f"{'stamping' if args.apply else 'would stamp'} {len(changed)} items")
    if args.apply and changed:
        def put_item(path: str, item: dict) -> None:
            gcs.put_bytes(json.dumps(item, indent=2).encode(), path,
                          content_type="application/geo+json", cache_control=gcs.CACHE_CATALOG)
        with ThreadPoolExecutor(max_workers=64) as ex:
            list(ex.map(put_item, changed.keys(), changed.values()))
        stac.refresh_catalog()
    return rc


if __name__ == "__main__":
    sys.exit(main())
