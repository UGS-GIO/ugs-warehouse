"""Prune orphaned publication STAC items (case twins, renamed IDs) after a reingest.

Compare published item objects under publication collections against the IDs produced
by current source data. Orphans are deleted so refresh_catalog does not retain stale
items.

An orphan is deleted only when one of its files is still in the source under another id: a case
twin or a renamed id. An orphan whose files the source no longer lists is a document missing from
the source, not a stale copy, so it is reported and kept. The guard on top refuses to delete more
than MAX_ORPHAN_SHARE of the source, so an empty or partial source cannot wipe the catalog.

Dry-run by default. The pubs pipeline runs it with --apply before the pubs ingest, whose catalog
refresh then drops the deleted items.

    python -m ugs_warehouse.pubs.prune            # list what would go
    python -m ugs_warehouse.pubs.prune --apply    # delete
"""
from __future__ import annotations

import argparse
import json
import sys

from ..core import config, gcs
from . import identity, sink_stac, source

MAX_ORPHAN_SHARE = 0.05

PUB_GROUPS = (
    identity.PUBLICATIONS_COLLECTION,
    identity.MINING_DISTRICT_COLLECTION,
    identity.EXTERNAL_COLLECTION,
)


def expected_pub_dirs(pubs: list[dict] | None = None) -> set[str]:
    """Layout paths (`<group>/<SERIES>/<SID>`) of every publication the current source produces."""
    if pubs is None:
        pubs = source.read_pubs()
    return {sink_stac.pub_collection_path(p) for p in pubs if (p.get("series_id") or "").strip()}


def source_files(pubs: list[dict], attachments: list[dict]) -> set[str]:
    """Every file URL the source lists, normalized the way the item builder writes hrefs."""
    return {u for r in [*pubs, *attachments] if (u := sink_stac.href(r.get("pub_url")))}


def item_files(item: dict) -> set[str]:
    """An item's file URLs, including the publisher copy of a file mirrored to our CDN."""
    out = set()
    for a in (item.get("assets") or {}).values():
        out.add(a.get("href"))
        out.add(((a.get("alternate") or {}).get("publisher") or {}).get("href"))
    return out - {None}


def renamed(orphan_dirs: set[str], files: set[str]) -> set[str]:
    """The orphan publication dirs whose files the source still lists under another id."""
    out = set()
    for d in orphan_dirs:
        doc = None
        for name in ("collection.json", f"{d.rsplit('/', 1)[1]}.json"):
            try:
                doc = json.loads(gcs.get_bytes(f"{d}/{name}"))
                break
            except (FileNotFoundError, ValueError):
                continue
        if doc and item_files(doc) & files:
            out.add(d)
    return out


def _pub_dir(group: str, rest: str) -> str | None:
    """The `<group>/<SERIES>/<SID>` a listed object belongs to, or None for a series-level file."""
    parts = rest.split("/")
    return f"{group}/{parts[0]}/{parts[1]}" if len(parts) >= 3 else None


def orphan_pub_paths(expected: set[str] | None = None, groups: tuple[str, ...] = PUB_GROUPS) -> list[str]:
    """Every object under a publication dir the current source does not produce."""
    if expected is None:
        expected = expected_pub_dirs()
    orphans: list[str] = []
    for group in groups:
        root = f"{config.STAC_PREFIX}/{group}/"
        for path in gcs.list_paths(root):
            d = _pub_dir(group, path[len(root):])
            if d and d not in expected:
                orphans.append(path)
    return orphans


_LEGACY_SERIES_FILES = {"collection.json", "items.json", "items.parquet"}


def legacy_layout_paths(expected: set[str] | None = None, groups: tuple[str, ...] = PUB_GROUPS) -> list[str]:
    """Files of the old layout that the new one leaves behind: a publication's old Item
    (`<SID>.json`) and ISO sidecar inside its new Collection dir, and each series' old
    collection.json/items.json/items.parquet beside its new catalog.json."""
    if expected is None:
        expected = expected_pub_dirs()
    out: list[str] = []
    for group in groups:
        root = f"{config.STAC_PREFIX}/{group}/"
        for path in gcs.list_paths(root):
            parts = path[len(root):].split("/")
            if len(parts) == 2 and parts[1] in _LEGACY_SERIES_FILES:
                out.append(path)
            elif (len(parts) == 3 and f"{group}/{parts[0]}/{parts[1]}" in expected
                  and parts[2] in (f"{parts[1]}.json", f"{parts[1]}.iso.xml")):
                out.append(path)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Prune orphaned publication STAC items")
    ap.add_argument("--apply", action="store_true", help="delete (default: dry-run)")
    ap.add_argument("--legacy", action="store_true",
                    help="also delete files left by the old publication layout (with --apply)")
    args = ap.parse_args()

    print("[prune-pubs] reading expected publications from source...")
    pubs = source.read_pubs()
    expected = expected_pub_dirs(pubs)
    print(f"[prune-pubs] expected {len(expected)} distinct publications")

    print("[prune-pubs] scanning published objects in GCS...")
    orphans = orphan_pub_paths(expected=expected)
    rc = _prune_orphans(args, pubs, expected, orphans) if orphans else 0
    if rc:
        return rc
    if not orphans:
        print("[prune-pubs] nothing stale under publication collections")

    if args.legacy:
        legacy = legacy_layout_paths(expected=expected)
        print(f"[prune-pubs] {len(legacy)} file(s) from the old layout")
        for path in legacy:
            print(f"  {'delete' if args.apply else 'would delete'} {path}")
            if args.apply:
                gcs.delete(path)

    if not args.apply:
        print("[prune-pubs] dry-run — re-run with --apply")
    else:
        print("[prune-pubs] done; the next catalog refresh drops the deleted items")
    return 0


def _prune_orphans(args: argparse.Namespace, pubs: list[dict], expected: set[str], orphans: list[str]) -> int:
    """Report orphan publication dirs and, with --apply, delete the renamed ones. Returns an exit code."""
    prefix = f"{config.STAC_PREFIX}/"
    orphan_dirs = {prefix + d for p in orphans if (d := _pub_dir_of(p, prefix))}
    stale = renamed(orphan_dirs, source_files(pubs, source.read_attachments()))
    kept = sorted(orphan_dirs - stale)
    print(f"[prune-pubs] {len(orphan_dirs)} orphan publication(s): {len(stale)} renamed (files still in "
          f"the source), {len(kept)} kept (files not in the source)")
    for d in kept[:20]:
        print(f"  keep {d}")
    if args.apply and len(stale) > MAX_ORPHAN_SHARE * max(len(expected), 1):
        print(f"[prune-pubs] REFUSE: {len(stale)} publications is more than {MAX_ORPHAN_SHARE:.0%} "
              f"of {len(expected)}; check the source before deleting", file=sys.stderr)
        return 1
    for path in (p for p in orphans if prefix + (_pub_dir_of(p, prefix) or "") in stale):
        print(f"  {'delete' if args.apply else 'would delete'} {path}")
        if args.apply:
            gcs.delete(path)
    return 0


def _pub_dir_of(path: str, prefix: str) -> str | None:
    """`<group>/<SERIES>/<SID>` of a full object path under `prefix`."""
    parts = path[len(prefix):].split("/")
    return "/".join(parts[:3]) if len(parts) >= 4 else None


if __name__ == "__main__":
    sys.exit(main())
