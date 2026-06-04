"""Per-topic ingest orchestrator.

For each topic:
  1. Read `{schema}.{topic}_current` from Postgres (`source`)
  2. Transform in DuckDB: hydrate WKB, confirm/reproject -> 4326, add h3_r9,
     hilbert-sort (`transform`)
  3. Write Iceberg table — WKB geom (`sink_iceberg`)
  4. Emit GeoParquet archive — native geom, citable (`sink_archive`)
  5. Build PMTiles via tippecanoe (`sink_pmtiles`)
  6. Write STAC item (`sink_stac`)

Each sink is independent: failure in one does not corrupt the others.
"""
from __future__ import annotations

import argparse
import sys
import traceback

from . import sink_archive, sink_iceberg, sink_pmtiles, sink_stac, source, transform
from .topics import REGISTRY, Topic, all_topics


def _ingest(topic: Topic, dry_run: bool = False) -> int:
    print(f"[{topic.fqn}] reading from Postgres")
    arrow_in = source.read(topic)
    print(f"[{topic.fqn}] {arrow_in.num_rows} rows in, {len(arrow_in.column_names)} columns")

    con, view = transform.run(arrow_in)

    if dry_run:
        # Validate source + transform without touching any sink.
        count = con.execute(f"SELECT count(*) FROM {view}").fetchone()[0]
        bbox = con.execute(f"""
            SELECT
              MIN(ST_XMin(geom)), MIN(ST_YMin(geom)),
              MAX(ST_XMax(geom)), MAX(ST_YMax(geom))
            FROM {view}
        """).fetchone()
        sample = con.execute(f"SELECT * EXCLUDE (geom) FROM {view} LIMIT 1").fetchone()
        print(f"[{topic.fqn}] DRY-RUN OK")
        print(f"  rows after transform : {count}")
        print(f"  bbox (4326)          : minx={bbox[0]:.6f} miny={bbox[1]:.6f} "
              f"maxx={bbox[2]:.6f} maxy={bbox[3]:.6f}")
        print(f"  columns              : {con.execute(f'DESCRIBE {view}').df()['column_name'].tolist()}")
        print(f"  sample row (no geom) : {sample}")
        return 0

    rc = 0
    for name, fn in [
        ("iceberg", lambda: sink_iceberg.write(topic, con, view)),
        ("archive", lambda: sink_archive.write(topic, con, view)),
        ("pmtiles", lambda: sink_pmtiles.build(topic, con, view)),
        ("stac",    lambda: sink_stac.write(topic, con, view)),
    ]:
        try:
            fn()
        except Exception as e:
            # Per-sink isolation: log + continue, never silent fail.
            print(f"[{topic.fqn}] sink {name} FAILED: {e}", file=sys.stderr)
            traceback.print_exc()
            rc = 1
    return rc


def ingest_topic(layer: str, dry_run: bool = False) -> int:
    topic = REGISTRY.get(layer)
    if topic is None:
        print(f"ERROR: unknown topic '{layer}'", file=sys.stderr)
        return 1
    try:
        return _ingest(topic, dry_run=dry_run)
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
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="exercise source + transform only; skip all sinks "
             "(no GCS / Iceberg writes — safe smoke against real _current)",
    )
    args = ap.parse_args()

    if args.all:
        rc = 0
        for t in all_topics():
            rc |= ingest_topic(t.layer, dry_run=args.dry_run)
        return rc
    return ingest_topic(args.topic, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
