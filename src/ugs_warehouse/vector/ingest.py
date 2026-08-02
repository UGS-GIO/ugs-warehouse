"""Per-topic ingest orchestrator.

For each topic:
  1. Read `{schema}.{topic}_current` from Postgres (`source`)
  2. Transform in DuckDB: hydrate WKB, confirm/reproject -> 4326, hilbert-sort (`transform`)
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
from types import ModuleType

from ..core import stac
from . import (
    fingerprint,
    related,
    sink_archive,
    sink_ducklake,
    sink_pmtiles,
    sink_stac,
    topics,
)
from .topics import Topic


def _backend() -> ModuleType:
    """The source backend: direct Postgres (Cloud SQL). PostgREST is the public viewer's runtime
    API, NOT an ingest source — the warehouse reads Postgres and writes parquet/pmtiles/STAC."""
    from . import source
    return source


def _related(topic: Topic) -> dict:
    """Resolve a topic's FK relationships from the registry → STAC related links + `ugs:foreign_keys`
    + materialised aspatial related assets. Best-effort: empty pieces when there are
    none (or the registry/grant is absent), never sinks the parent ingest."""
    try:
        return related.resolve(topic)
    except Exception as e:  # noqa: BLE001 — relationships never block the parent ingest
        print(f"[{topic.fqn}] relationships FAILED: {e}", file=sys.stderr)
        return {}


def _run_sinks(topic: Topic, con, view: str, backend, dry_run: bool, skip_refresh: bool,
               skip_unchanged: bool = True) -> int:
    """Geometry check → dry-run report → sinks (ducklake/archive/pmtiles/stac) over the
    transformed table → catalog refresh.

    `skip_unchanged` (default on; `--force` turns it off) compares a content fingerprint of the
    transformed view to the published STAC item; an exact match (with the PMTiles still present)
    skips the DATA sinks — the expensive tippecanoe rebuild would produce byte-identical output.
    The STAC sink still runs, since curated registry metadata changes without the data changing
    (#54); rewriting an item + ISO sidecar is two small object writes, never what the skip
    protected."""
    count = con.execute(f"SELECT count(*) FROM {view}").fetchone()[0]
    non_null = con.execute(f"SELECT count(*) FROM {view} WHERE geom IS NOT NULL").fetchone()[0]
    if non_null == 0:
        cols = [r[0] for r in con.execute(f"DESCRIBE {view}").fetchall()]
        print(f"[{topic.fqn}] SKIP: 0/{count} rows have geometry "
              f"(non-spatial table, or geom not readable by this backend)", file=sys.stderr)
        print(f"  columns : {cols}", file=sys.stderr)
        return 1

    if dry_run:
        cols = [r[0] for r in con.execute(f"DESCRIBE {view}").fetchall()]
        sample = con.execute(f"SELECT * EXCLUDE (geom) FROM {view} LIMIT 1").fetchone()
        bbox = con.execute(f"SELECT MIN(ST_XMin(geom)), MIN(ST_YMin(geom)), "
                           f"MAX(ST_XMax(geom)), MAX(ST_YMax(geom)) FROM {view}").fetchone()
        print(f"[{topic.fqn}] DRY-RUN OK")
        print(f"  rows after transform : {count} ({non_null} with geometry)")
        print(f"  bbox (4326)          : minx={bbox[0]:.6f} miny={bbox[1]:.6f} "
              f"maxx={bbox[2]:.6f} maxy={bbox[3]:.6f}")
        print(f"  columns              : {cols}")
        print(f"  sample row (no geom) : {sample}")
        return 0

    # Content fingerprint — always computed (so every run records a fresh `ugs:content_hash`, even a
    # forced one, keeping future skips correct). Only acted on when --skip-unchanged is set.
    fp = fingerprint.compute(con, view)
    unchanged = skip_unchanged and fingerprint.is_unchanged(topic, fp)
    if unchanged:
        print(f"[{topic.fqn}] UNCHANGED: content + tiling identical to the published item — "
              f"skipping the data sinks (no tile rebuild), rewriting STAC + ISO")

    meta = backend.read_metadata(topic)
    related_info = _related(topic)
    rc = 0
    # The data sinks are what the fingerprint gates. The STAC sink always runs: its inputs include
    # curated registry metadata, which an editor changes without touching a single row (#54). It
    # reads the view and derives its asset hrefs from config, so it does not depend on the skipped
    # sinks having run — on an unchanged topic those artifacts are already published, which is
    # exactly what `is_unchanged` (which also verifies the PMTiles) established.
    data_sinks = [] if unchanged else [
        ("ducklake", lambda: sink_ducklake.write(topic, con, view)),
        ("archive",  lambda: sink_archive.write(topic, con, view)),
        ("pmtiles",  lambda: sink_pmtiles.build(topic, con, view)),
    ]
    for name, fn in [
        *data_sinks,
        ("stac",     lambda: sink_stac.write(topic, con, view, metadata=meta, related=related_info,
                                             content_hash=fp)),
    ]:
        try:
            fn()
        except Exception as e:  # per-sink isolation: log + continue, never silent fail
            print(f"[{topic.fqn}] sink {name} FAILED: {e}", file=sys.stderr)
            traceback.print_exc()
            rc = 1

    if not skip_refresh:
        try:
            stac.refresh_catalog()
        except Exception as e:
            print(f"[{topic.fqn}] stac catalog refresh FAILED: {e}", file=sys.stderr)
            traceback.print_exc()
            rc = 1
    return rc


def _ingest(topic: Topic, dry_run: bool = False, skip_refresh: bool = False,
            skip_unchanged: bool = True) -> int:
    backend = _backend()
    # One DuckDB connection streams Postgres scan → transform → materialize (no pyarrow, no
    # Python-held rows; DuckDB spills under its memory cap). The sinks read the materialized table.
    print(f"[{topic.fqn}] reading from Postgres (streaming, single DuckDB)")
    con, view = backend.stream_transformed(topic)
    # The sinks read `view` through `con`, so the close waits until they're done. Without it the
    # Cloud Run handler leaks a connection — ATTACHed Postgres + materialized table — per push.
    try:
        return _run_sinks(topic, con, view, backend, dry_run, skip_refresh, skip_unchanged)
    finally:
        con.close()


def ingest_topic(topic: Topic, dry_run: bool = False, skip_refresh: bool = False,
                 skip_unchanged: bool = True) -> int:
    """Ingest a single Topic. Top-level entry point for callers (CLI + service).

    `skip_unchanged` defaults on — an unchanged topic (fingerprint matches the published item) is
    skipped without rebuilding tiles. Pass `skip_unchanged=False` (CLI `--force`) to always rebuild."""
    try:
        return _ingest(topic, dry_run=dry_run, skip_refresh=skip_refresh,
                       skip_unchanged=skip_unchanged)
    except Exception as e:
        # Check if it's the "FATAL: no GEOMETRY column found" case (from source.py or transform.py)
        # We can handle non-spatial gracefully by just returning 0 (success, nothing to ingest)
        if "no GEOMETRY column found" in str(e):
            print(f"[{topic.fqn}] SKIP: non-spatial table (no GEOMETRY column)", file=sys.stderr)
            return 0
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
    ap.add_argument(
        "--parallel",
        action="store_true",
        help="ingest discovered topics in parallel using a ThreadPoolExecutor",
    )
    ap.add_argument(
        "--workers",
        type=int,
        default=4,
        help="number of concurrent worker threads when running in parallel (default: 4)",
    )
    ap.add_argument(
        "--skip-refresh",
        action="store_true",
        help="skip the final STAC catalog refresh",
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help="rebuild every topic even when its content + tiling fingerprint is unchanged. "
             "Default is to SKIP unchanged topics (no tile rebuild); the first run after deploy "
             "still rebuilds everything since no published item carries a fingerprint yet",
    )
    args = ap.parse_args()
    skip_unchanged = not args.force

    if args.all:
        backend = _backend()
        discovered = backend.discover()
        print(f"discovered {len(discovered)} topics in {topics.MART_SCHEMAS}")
        rc = 0

        # Skip individual refreshes when running all topics to avoid redundant catalog listings/writes.
        # We will trigger exactly one final refresh at the end instead.
        skip_indiv = True

        if args.parallel:
            from concurrent.futures import ThreadPoolExecutor
            print(f"running ingestion in parallel with {args.workers} workers...")
            with ThreadPoolExecutor(max_workers=args.workers) as executor:
                def run_one(t: Topic) -> int:
                    return ingest_topic(t, dry_run=args.dry_run, skip_refresh=skip_indiv,
                                        skip_unchanged=skip_unchanged)
                results = list(executor.map(run_one, discovered))
            rc = 1 if any(r != 0 for r in results) else 0
        else:
            for t in discovered:
                rc |= ingest_topic(t, dry_run=args.dry_run, skip_refresh=skip_indiv,
                                   skip_unchanged=skip_unchanged)

        if not args.skip_refresh:
            try:
                print("running final STAC catalog refresh...")
                stac.refresh_catalog()
            except Exception as e:
                print(f"final stac catalog refresh FAILED: {e}", file=sys.stderr)
                traceback.print_exc()
                rc = 1
        return rc

    topic = Topic.parse(args.topic)
    return ingest_topic(topic, dry_run=args.dry_run, skip_refresh=args.skip_refresh,
                        skip_unchanged=skip_unchanged)


if __name__ == "__main__":
    sys.exit(main())
