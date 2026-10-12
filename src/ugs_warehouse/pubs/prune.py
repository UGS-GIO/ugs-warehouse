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


def delete_verified(paths: list[str]) -> None:
    """Delete objects, then re-list their directories and raise if any is still there.

    `gcs.delete` swallows every error, so a failed delete would otherwise look like a success."""
    if not paths:
        return
    for path in paths:
        gcs.delete(path)
    present = {q for d in {p.rsplit("/", 1)[0] + "/" for p in paths} for q in gcs.list_paths(d)}
    left = sorted(set(paths) & present)
    if left:
        raise RuntimeError(f"{len(left)} object(s) still present after delete: {left}")


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


def _layout_dir(path: str) -> str | None:
    """`<group>/<SERIES>/<SID>` of a full object path, or None for a series- or group-level file."""
    prefix = f"{config.STAC_PREFIX}/"
    parts = path[len(prefix):].split("/") if path.startswith(prefix) else []
    return "/".join(parts[:3]) if len(parts) >= 4 else None


def orphan_pub_paths(expected: set[str] | None = None, groups: tuple[str, ...] = PUB_GROUPS) -> list[str]:
    """Every object under a publication dir the current source does not produce."""
    if expected is None:
        expected = expected_pub_dirs()
    orphans: list[str] = []
    for group in groups:
        for path in gcs.list_paths(f"{config.STAC_PREFIX}/{group}/"):
            d = _layout_dir(path)
            if d and d not in expected:
                orphans.append(path)
    return orphans


_LEGACY_SERIES_FILES = ("collection.json", "items.json", "items.parquet")


def _legacy_scan(expected: set[str], groups: tuple[str, ...]) -> tuple[list[str], int]:
    """Old-layout files the new layout has replaced, and how many series were seen.

    A pub's old Item `<SID>.json` is listed only beside its new `collection.json`, and a series'
    old collection/items files only beside its new `catalog.json`, so nothing is removed before
    its replacement exists. The pub ISO sidecar is the new Collection's own record, so it stays.
    """
    out: list[str] = []
    series: set[tuple[str, str]] = set()
    for group in groups:
        root = f"{config.STAC_PREFIX}/{group}/"
        listing = set(gcs.list_paths(root))
        for path in sorted(listing):
            parts = path[len(root):].split("/")
            if len(parts) >= 2:
                series.add((group, parts[0]))
            if len(parts) == 2 and parts[1] in _LEGACY_SERIES_FILES and f"{root}{parts[0]}/catalog.json" in listing:
                out.append(path)
            elif (len(parts) == 3 and f"{group}/{parts[0]}/{parts[1]}" in expected
                  and parts[2] == f"{parts[1]}.json" and f"{root}{parts[0]}/{parts[1]}/collection.json" in listing):
                out.append(path)
    return out, len(series)


def legacy_layout_paths(expected: set[str] | None = None, groups: tuple[str, ...] = PUB_GROUPS) -> list[str]:
    """Old-layout files (pub Item `<SID>.json`, series collection/items files) whose replacement exists."""
    if expected is None:
        expected = expected_pub_dirs()
    return _legacy_scan(expected, groups)[0]


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
    if orphans:
        rc = _prune_orphans(args, pubs, expected, orphans)
        if rc:
            return rc
    else:
        print("[prune-pubs] nothing stale under publication collections")

    if args.legacy:
        rc = _prune_legacy(args, expected)
        if rc:
            return rc

    if not args.apply:
        print("[prune-pubs] dry-run — re-run with --apply")
    else:
        print("[prune-pubs] done; the next catalog refresh drops the deleted items")
    return 0


def _prune_orphans(args: argparse.Namespace, pubs: list[dict], expected: set[str], orphans: list[str]) -> int:
    """Report orphan publication dirs and, with --apply, delete the renamed ones. Returns an exit code."""
    prefix = f"{config.STAC_PREFIX}/"
    orphan_dirs = {prefix + d for p in orphans if (d := _layout_dir(p))}
    stale = renamed(orphan_dirs, source_files(pubs, source.read_attachments()))
    kept = sorted(orphan_dirs - stale)
    print(f"[prune-pubs] {len(orphan_dirs)} orphan publication(s): {len(stale)} renamed (files still in "
          f"the source), {len(kept)} kept (files not in the source)")
    for d in kept[:20]:
        print(f"  keep {d}")
    if len(stale) > MAX_ORPHAN_SHARE * max(len(expected), 1):
        print(f"[prune-pubs] {'REFUSE' if args.apply else 'would REFUSE'}: {len(stale)} publications is "
              f"more than {MAX_ORPHAN_SHARE:.0%} of {len(expected)}; check the source before deleting",
              file=sys.stderr)
        if args.apply:
            return 1
    doomed = [p for p in orphans if prefix + (_layout_dir(p) or "") in stale]
    for path in doomed:
        print(f"  {'delete' if args.apply else 'would delete'} {path}")
    if args.apply:
        delete_verified(doomed)
    return 0


def _prune_legacy(args: argparse.Namespace, expected: set[str]) -> int:
    """Report old-layout files and, with --apply, delete them. Returns an exit code."""
    legacy, n_series = _legacy_scan(expected, PUB_GROUPS)
    bound = len(expected) + 3 * n_series
    print(f"[prune-pubs] {len(legacy)} file(s) from the old layout")
    if len(legacy) > bound:
        print(f"[prune-pubs] {'REFUSE' if args.apply else 'would REFUSE'}: {len(legacy)} old-layout files "
              f"exceeds the {bound} expected for {len(expected)} publications and {n_series} series; "
              f"check the listing before deleting", file=sys.stderr)
        if args.apply:
            return 1
    for path in legacy:
        print(f"  {'delete' if args.apply else 'would delete'} {path}")
    if args.apply:
        delete_verified(legacy)
    return 0


if __name__ == "__main__":
    sys.exit(main())
