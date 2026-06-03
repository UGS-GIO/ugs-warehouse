"""Per-topic ingest orchestrator.

For each topic:
  1. Read `{schema}.{topic}_current` from Postgres (`source`)
  2. Transform in DuckDB: hydrate WKB, confirm/reproject -> 4326, add h3_r9,
     hilbert-sort (`transform`)
  3. Write Iceberg table — WKB geom (`sink_iceberg`)
  4. Emit GeoParquet archive — native geom, citable (`sink_archive`)  [TODO]
  5. Build PMTiles via tippecanoe (`sink_pmtiles`)                      [TODO]
  6. Write STAC item (`sink_stac`)                                      [TODO]

Each sink is independent: failure in one does not corrupt the others.
"""
from __future__ import annotations

import argparse
import sys
import traceback

from . import sink_archive, sink_iceberg, source, transform
from .topics import REGISTRY, Topic, all_topics


def _ingest(topic: Topic) -> int:
    print(f"[{topic.fqn}] reading from Postgres")
    arrow_in = source.read(topic)
    print(f"[{topic.fqn}] {arrow_in.num_rows} rows in")

    con, view = transform.run(arrow_in)

    rc = 0
    for name, fn in [
        ("iceberg", lambda: sink_iceberg.write(topic, con, view)),
        ("archive", lambda: sink_archive.write(topic, con, view)),
        # ("pmtiles",  lambda: sink_pmtiles.build(topic, ...)),
        # ("stac",     lambda: sink_stac.write(topic, ...)),
    ]:
        try:
            fn()
        except Exception as e:
            # Per-sink isolation: log + continue, never silent fail.
            print(f"[{topic.fqn}] sink {name} FAILED: {e}", file=sys.stderr)
            traceback.print_exc()
            rc = 1
    return rc


def ingest_topic(layer: str) -> int:
    topic = REGISTRY.get(layer)
    if topic is None:
        print(f"ERROR: unknown topic '{layer}'", file=sys.stderr)
        return 1
    try:
        return _ingest(topic)
    except Exception as e:
        print(f"[{topic.fqn}] FATAL: {e}", file=sys.stderr)
        traceback.print_exc()
        return 1


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
