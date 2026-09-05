"""Prune orphaned publication STAC items (case twins, renamed IDs) after a reingest.

Compare published item objects under publication collections against the IDs produced
by current source data. Orphans are deleted so refresh_catalog does not retain stale
items.

Dry-run by default. Run AFTER ugs-pubs-ingest has published the corrected items,
then re-run `python -m scripts.refresh_stac`.

    python -m scripts.prune_pub_items            # list what would go
    python -m scripts.prune_pub_items --apply    # delete
"""
from __future__ import annotations

import argparse
import sys

from ugs_warehouse.core import config, gcs
from ugs_warehouse.pubs import identity, sink_stac, source

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
    expected = expected_pub_items()
    print(f"[prune-pubs] expected {len(expected)} distinct pub items")

    print("[prune-pubs] scanning published objects in GCS...")
    orphans = orphan_pub_paths(expected=expected)
    if not orphans:
        print("[prune-pubs] nothing stale under publication collections")
        return 0

    orphan_dirs = {p.rsplit("/", 1)[0] for p in orphans}
    print(f"[prune-pubs] {len(orphans)} orphan object(s) across {len(orphan_dirs)} item(s)")
    for path in orphans:
        print(f"  {'delete' if args.apply else 'would delete'} {path}")
        if args.apply:
            gcs.delete(path)

    if not args.apply:
        print("[prune-pubs] dry-run — re-run with --apply")
    else:
        print("[prune-pubs] done — now re-run `python -m scripts.refresh_stac`")
    return 0


if __name__ == "__main__":
    sys.exit(main())
