"""Regenerate the static root STAC catalog on demand.

Lists the per-topic item files already in GCS and rewrites `catalog.json` to
match — the same `sink_stac.refresh_catalog()` that runs automatically after
each ingest. Useful for backfilling the catalog or repairing it after manual
GCS edits, without re-running an ingest.

Reads/writes the STAC bucket+prefix (WAREHOUSE_STAC_{BUCKET,PREFIX}); needs GCS
write auth (ADC). Does NOT need WAREHOUSE_PUBLIC_BASE_URL — item asset hrefs are
fixed at ingest time; the catalog only holds relative links to the items.

    python -m scripts.refresh_stac
"""
from __future__ import annotations

import sys

from ugs_warehouse import sink_stac


def main() -> int:
    sink_stac.refresh_catalog()
    return 0


if __name__ == "__main__":
    sys.exit(main())
