"""Regenerate the static root STAC catalog on demand.

Lists the per-topic item files already in GCS and rewrites `catalog.json` to
match — the same `sink_stac.refresh_catalog()` that runs automatically after
each ingest. Useful for backfilling the catalog or repairing it after manual
GCS edits, without re-running an ingest.

Reads/writes the STAC bucket+prefix (WAREHOUSE_STAC_{BUCKET,PREFIX}); needs GCS
write auth (ADC). Does NOT need WAREHOUSE_PUBLIC_BASE_URL — item asset hrefs are
fixed at ingest time; the catalog only holds relative links to the items.

    python -m scripts.refresh_stac

Then invalidates on the CDN the layers a job marked pending (core/cdn.py), e.g. an ops-console
override edit. Exits 1 if that fails; the layers stay pending.
"""
from __future__ import annotations

import sys

from ugs_warehouse.core import cdn, stac


def main() -> int:
    problems = stac.refresh_catalog()
    try:
        cdn.flush(problems)
    except Exception as e:  # noqa: BLE001 — the catalog is refreshed; the layers stay pending
        print(f"[refresh] CDN invalidation FAILED: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
