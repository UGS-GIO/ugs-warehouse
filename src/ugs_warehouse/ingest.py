"""Per-topic ingest orchestrator.

For each topic:
  1. Read `{schema}.{topic}_current` from Postgres (`source`)
  2. Transform in DuckDB: hydrate WKB, confirm/reproject -> 4326, add h3_r9,
     hilbert-sort (`transform`)
  3. Write DuckLake table — native geom (`sink_ducklake`)
  4. Emit GeoParquet archive — native geom, citable (`sink_archive`)
  5. Build PMTiles via tippecanoe (`sink_pmtiles`)
  6. Write STAC item (`sink_stac`)

Each sink is independent: failure in one does not corrupt the others.

Topics are not hard-coded — `--all` discovers `_current` tables from Postgres,
and `--topic schema.layer_current` ingests one explicitly.
"""
from __future__ import annotations

import argparse
import sys
import traceback

from . import sink_archive, sink_ducklake, sink_pmtiles, sink_stac, source, topics, transform
from .topics import Topic


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
        cols = [r[0] for r in con.execute(f"DESCRIBE {view}").fetchall()]
        sample = con.execute(f"SELECT * EXCLUDE (geom) FROM {view} LIMIT 1").fetchone()
        print(f"[{topic.fqn}] DRY-RUN OK")
        print(f"  rows after transform : {count}")
        print(f"  bbox (4326)          : minx={bbox[0]:.6f} miny={bbox[1]:.6f} "
              f"maxx={bbox[2]:.6f} maxy={bbox[3]:.6f}")
        print(f"  columns              : {cols}")
        print(f"  sample row (no geom) : {sample}")
        return 0

    rc = 0
    for name, fn in [
        ("ducklake", lambda: sink_ducklake.write(topic, con, view)),
        ("archive",  lambda: sink_archive.write(topic, con, view)),
        ("pmtiles",  lambda: sink_pmtiles.build(topic, con, view)),
        ("stac",     lambda: sink_stac.write(topic, con, view)),
    ]:
        try:
            fn()
        except Exception as e:
            # Per-sink isolation: log + continue, never silent fail.
            print(f"[{topic.fqn}] sink {name} FAILED: {e}", file=sys.stderr)
            traceback.print_exc()
            rc = 1
    return rc


def ingest_topic(topic: Topic, dry_run: bool = False) -> int:
    """Ingest a single Topic. Top-level entry point for callers (CLI + service)."""
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
        help="dotted form: schema.layer_current "
             "(e.g. hazards.hazards_qfaults_current)",
    )
    g.add_argument(
        "--all",
        action="store_true",
        help="discover every _current table in the mart schemas and ingest each",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="exercise source + transform only; skip all sinks "
             "(no GCS / Iceberg writes — safe smoke against real _current)",
    )
    args = ap.parse_args()

    if args.all:
        con = source._connect()
        discovered = topics.discover(con)
        print(f"discovered {len(discovered)} topics in {topics.MART_SCHEMAS}")
        rc = 0
        for t in discovered:
            rc |= ingest_topic(t, dry_run=args.dry_run)
        return rc

    return ingest_topic(Topic.parse(args.topic), dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
