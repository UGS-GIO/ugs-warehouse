"""Delete the pre-split flat serving-topic objects after a reingest into the per-schema layout.

Vector items moved from `ugs-serving-topics/<id>/<id>.json` to
`ugs-serving-topics/<schema>/<id>/<id>.json`. A reingest writes the new objects but can't remove
the old ones, and `refresh_catalog()` derives collections FROM the objects — so until the flat
copies go, the catalog carries both a flat `ugs-serving-topics` collection and the sub-catalog,
each claiming the same items.

Removes, under the serving-topics prefix:
  - flat item docs + their ISO sidecars   (`<catalog>/<id>/…`, one level above the nested layout)
  - the stale flat `collection.json`      (replaced by `catalog.json` + per-schema collections)

Dry-run by default. Run AFTER a full reingest has published the nested items, then re-run
`python -m scripts.refresh_stac`.

    python -m scripts.prune_flat_topic_items            # list what would go
    python -m scripts.prune_flat_topic_items --apply    # delete
"""
from __future__ import annotations

import argparse
import sys

from ugs_warehouse.core import config, gcs
from ugs_warehouse.vector.sink_stac import CATALOG


def _stale_paths() -> tuple[list[str], list[str]]:
    """(flat item objects, stale collection doc) under the serving-topics prefix.

    Nested items sit at `<prefix>/<catalog>/<schema>/<id>/<id>.json` — 3 segments below the
    catalog. Anything only 2 below is a leftover from the flat layout.
    """
    root = f"{config.STAC_PREFIX}/{CATALOG}/"
    flat, docs = [], []
    for path in gcs.list_paths(root):
        rest = path[len(root):]
        parts = rest.split("/")
        if len(parts) == 1:
            if parts[0] == "collection.json":  # flat-layout collection doc; catalog.json replaces it
                docs.append(path)
            continue
        # `<id>/<id>.json` or `<id>/<id>.iso.xml` — a flat item and its sidecars.
        if len(parts) == 2 and parts[1].startswith(parts[0] + "."):
            flat.append(path)
    return flat, docs


def main() -> int:
    ap = argparse.ArgumentParser(description="Prune pre-split flat serving-topic STAC objects")
    ap.add_argument("--apply", action="store_true", help="delete (default: dry-run)")
    args = ap.parse_args()

    flat, docs = _stale_paths()
    targets = flat + docs
    if not targets:
        print(f"[prune] nothing stale under {config.STAC_PREFIX}/{CATALOG}/")
        return 0

    ids = {p[len(f"{config.STAC_PREFIX}/{CATALOG}/"):].split("/")[0] for p in flat}
    print(f"[prune] {len(flat)} flat object(s) across {len(ids)} item(s)"
          + (f" + {len(docs)} stale collection doc" if docs else ""))
    for path in targets:
        print(f"  {'delete' if args.apply else 'would delete'} {path}")
        if args.apply:
            gcs.delete(path)
    if not args.apply:
        print("[prune] dry-run — re-run with --apply")
    else:
        print("[prune] done — now re-run `python -m scripts.refresh_stac`")
    return 0


if __name__ == "__main__":
    sys.exit(main())
