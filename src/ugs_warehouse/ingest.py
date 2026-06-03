"""Per-topic ingest orchestrator.

For each topic:
  1. Read `{schema}.{topic}_current` from Postgres (`source`)
  2. Transform in DuckDB: confirm/reproject -> 4326, add h3_r9, hilbert-sort,
     ST_AsWKB for the Iceberg copy (`transform`)
  3. Write Iceberg table — WKB geom (`sink_iceberg`)
  4. Emit GeoParquet archive — native geom, citable (`sink_archive`)
  5. Build PMTiles via tippecanoe (`sink_pmtiles`)
  6. Write STAC item (`sink_stac`)

Each sink is independent: failure in one does not corrupt the others.
"""
from __future__ import annotations

import argparse
import sys

from .topics import REGISTRY, all_topics


def ingest_topic(layer: str) -> int:
    topic = REGISTRY.get(layer)
    if topic is None:
        print(f"ERROR: unknown topic '{layer}'", file=sys.stderr)
        return 1
    print(f"[{topic.fqn}] starting ingest")

    # TODO: source.read(topic) -> pyarrow.Table (native geom, source CRS)
    # TODO: transform.run(arrow) -> (arrow_native_geom_4326, arrow_iceberg_wkb)
    # TODO: sink_iceberg.write(topic, arrow_iceberg_wkb)
    # TODO: archive_path = sink_archive.write(topic, arrow_native_geom_4326)
    # TODO: sink_pmtiles.build(topic, archive_path)
    # TODO: sink_stac.write(topic)

    print(f"[{topic.fqn}] OK (stub — sinks not yet wired)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Warehouse per-topic ingest")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument(
        "--topic",
        help="single topic _current name (e.g. hazards_qfaults_current)",
    )
    g.add_argument(
        "--all",
        action="store_true",
        help="ingest every topic in the registry",
    )
    args = ap.parse_args()

    if args.all:
        rc = 0
        for t in all_topics():
            rc |= ingest_topic(t.layer)
        return rc
    return ingest_topic(args.topic)


if __name__ == "__main__":
    sys.exit(main())
