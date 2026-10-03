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


def expected_pub_items(pubs: list[dict] | None = None) -> set[tuple[str, str, str]]:
    """Return set of (collection_group, series_code, item_id) expected from current source."""
    if pubs is None:
        pubs = source.read_pubs()
    expected = set()
    for p in pubs:
        sid = (p.get("series_id") or "").strip()
        if not sid:
            continue
        group = sink_stac.collection_group(p)
        code = sink_stac.series_code(sid)
        item_id = sink_stac.item_id_for(sid)
        expected.add((group, code, item_id))
    return expected


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
    """The orphan item dirs whose files the source still lists under another id."""
    out = set()
    for d in orphan_dirs:
        try:
            item = json.loads(gcs.get_bytes(f"{d}/{d.rsplit('/', 1)[1]}.json"))
        except (FileNotFoundError, ValueError):
            continue
        if item_files(item) & files:
            out.add(d)
    return out


def orphan_pub_paths(
    expected: set[tuple[str, str, str]] | None = None,
    groups: tuple[str, ...] = PUB_GROUPS,
) -> list[str]:
    """Find all object paths in GCS publication collections whose item is not in `expected`."""
    if expected is None:
        expected = expected_pub_items()

    orphans: list[str] = []
    for group in groups:
        root = f"{config.STAC_PREFIX}/{group}/"
        for path in gcs.list_paths(root):
            rest = path[len(root):]
            parts = rest.split("/")
            # Item assets sit at <series_code>/<item_id>/<filename>
            if len(parts) == 3:
                code, item_id, _ = parts
                if (group, code, item_id) not in expected:
                    orphans.append(path)
    return orphans


def main() -> int:
    ap = argparse.ArgumentParser(description="Prune orphaned publication STAC items")
    ap.add_argument("--apply", action="store_true", help="delete (default: dry-run)")
    args = ap.parse_args()

    print("[prune-pubs] reading expected pub items from source...")
    pubs = source.read_pubs()
    expected = expected_pub_items(pubs)
    print(f"[prune-pubs] expected {len(expected)} distinct pub items")

    print("[prune-pubs] scanning published objects in GCS...")
    orphans = orphan_pub_paths(expected=expected)
    if not orphans:
        print("[prune-pubs] nothing stale under publication collections")
        return 0

    orphan_dirs = {p.rsplit("/", 1)[0] for p in orphans}
    stale = renamed(orphan_dirs, source_files(pubs, source.read_attachments()))
    kept = sorted(orphan_dirs - stale)
    print(f"[prune-pubs] {len(orphan_dirs)} orphan item(s): {len(stale)} renamed (files still in the "
          f"source), {len(kept)} kept (files not in the source)")
    for d in kept[:20]:
        print(f"  keep {d}")
    if args.apply and len(stale) > MAX_ORPHAN_SHARE * max(len(expected), 1):
        print(f"[prune-pubs] REFUSE: {len(stale)} items is more than {MAX_ORPHAN_SHARE:.0%} "
              f"of {len(expected)}; check the source before deleting", file=sys.stderr)
        return 1
    for path in (p for p in orphans if p.rsplit("/", 1)[0] in stale):
        print(f"  {'delete' if args.apply else 'would delete'} {path}")
        if args.apply:
            gcs.delete(path)

    if not args.apply:
        print("[prune-pubs] dry-run — re-run with --apply")
    else:
        print("[prune-pubs] done; the next catalog refresh drops the deleted items")
    return 0


if __name__ == "__main__":
    sys.exit(main())
