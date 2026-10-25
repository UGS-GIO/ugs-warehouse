"""Per-topic ingest orchestrator.

For each topic:
  1. Read `{schema}.{topic}_current` from Postgres (`source`)
  2. Transform in DuckDB: hydrate WKB, confirm/reproject -> 4326, hilbert-sort (`transform`)
  3. Write DuckLake table — native geom (`sink_ducklake`)
  4. Emit GeoParquet archive — native geom, citable (`sink_archive`)
  5. Build PMTiles via tippecanoe (`sink_pmtiles`)
  6. Write STAC item (`sink_stac`)
  7. Invalidate a changed layer's FeatureServer paths on the CDN (`core.cdn`), once the run is done

Each sink is independent: failure in one does not corrupt the others.

Topics are not hard-coded — `--all` discovers `_current` tables from Postgres,
and `--topic schema.layer_current` ingests one explicitly.
"""
from __future__ import annotations

import argparse
import logging
import sys
from types import ModuleType

from ..core import cdn, gcs, jobs, logs, stac
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

log = logging.getLogger("ugs-warehouse.ingest")

# The ingest job's --task-timeout is 3600s, so a lease older than that belongs to a dead run.
LOCK_TTL_S = 3600
LOCK_WAIT_S = 2700


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
        log.warning("[%s] relationships FAILED: %s", topic.fqn, e, extra={"topic": topic.fqn})
        return {}


def _preflight(topic: Topic, con, view: str, dry_run: bool) -> int | None:
    """Geometry check + dry-run report: an rc to stop with, or None to run the sinks."""
    count = con.execute(f"SELECT count(*) FROM {view}").fetchone()[0]
    non_null = con.execute(f"SELECT count(*) FROM {view} WHERE geom IS NOT NULL").fetchone()[0]
    if non_null == 0:
        cols = [r[0] for r in con.execute(f"DESCRIBE {view}").fetchall()]
        log.warning("[%s] SKIP: 0/%s rows have geometry "
                    "(non-spatial table, or geom not readable by this backend); columns: %s",
                    topic.fqn, count, cols, extra={"topic": topic.fqn})
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
    return None


def _data_sinks(topic: Topic, con, view: str, written: dict[str, gcs.FileMeta]) -> list:
    """The fingerprint-gated sinks as (name, fn); each records its write in `written`."""
    def _archive() -> None:
        written["data"] = sink_archive.write(topic, con, view)

    def _overview() -> None:
        # Only beside an archive written this run; when the archive failed the old set stays.
        if "data" in written:
            sink_archive.write_overviews(topic, con, view, written,
                                         sink_stac.published_overviews(topic))

    def _geojson() -> None:
        if "data" in written:
            sink_archive.write_geojson(topic, con, view, written,
                                       sink_stac.published_geojson(topic))

    def _pmtiles() -> None:
        written["pmtiles"] = sink_pmtiles.build(topic, con, view)

    return [
        ("ducklake", lambda: sink_ducklake.write(topic, con, view)),
        ("archive",  _archive),
        ("overview", _overview),
        ("geojson",  _geojson),
        ("pmtiles",  _pmtiles),
    ]


def _run_sink(topic: Topic, name: str, fn) -> bool:
    """Run one sink in isolation: a failure is logged and reported, never raised."""
    try:
        fn()
        return True
    except Exception:  # per-sink isolation: log + continue, never silent fail
        log.exception("[%s] sink %s FAILED", topic.fqn, name,
                      extra={"topic": topic.fqn, "sink": name})
        return False


def _refresh_catalog(topic: Topic) -> bool:
    """Rebuild the catalog; False (logged) on failure."""
    try:
        stac.refresh_catalog()
        return True
    except Exception:
        log.exception("[%s] stac catalog refresh FAILED", topic.fqn, extra={"topic": topic.fqn})
        return False


def _invalidate_cdn(stems: list[str]) -> int:
    """Invalidate the changed layers on the CDN; 1 (logged) on failure."""
    try:
        cdn.invalidate_features(stems)
        return 0
    except Exception:
        log.exception("CDN invalidation FAILED for %s", stems)
        return 1


def _run_sinks(topic: Topic, con, view: str, backend, dry_run: bool, skip_refresh: bool,
               skip_unchanged: bool = True, changed: list[str] | None = None) -> int:
    """Geometry check → dry-run report → sinks (ducklake/archive/pmtiles/stac) over the
    transformed table → catalog refresh.

    `skip_unchanged` (default on; `--force` turns it off) compares a content fingerprint of the
    transformed view to the published STAC item; an exact match (with the PMTiles still present)
    skips the DATA sinks — the expensive tippecanoe rebuild would produce byte-identical output.
    The STAC sink still runs, since curated registry metadata changes without the data changing
    (#54); rewriting an item + ISO sidecar is two small object writes, never what the skip
    protected."""
    rc = _preflight(topic, con, view, dry_run)
    if rc is not None:
        return rc

    # Content fingerprint — always computed (so every run records a fresh `ugs:content_hash`, even a
    # forced one, keeping future skips correct). Only acted on when --skip-unchanged is set.
    fp = fingerprint.compute(con, view)
    unchanged = skip_unchanged and fingerprint.is_unchanged(topic, fp)
    if unchanged:
        log.info("[%s] UNCHANGED: content + tiling identical to the published item — "
                 "skipping the data sinks (no tile rebuild), rewriting STAC + ISO",
                 topic.fqn, extra={"topic": topic.fqn})

    meta = backend.read_metadata(topic)
    related_info = _related(topic)
    # The data sinks are what the fingerprint gates. The STAC sink always runs: its inputs include
    # curated registry metadata, which an editor changes without touching a single row (#54). It
    # reads the view and derives its asset hrefs from config, so it does not depend on the skipped
    # sinks having run — on an unchanged topic those artifacts are already published, which is
    # exactly what `is_unchanged` (which also verifies the PMTiles) established.
    # Size + checksum come from the writes themselves, keyed by the asset that cites them. A sink
    # that was skipped (or failed) leaves its key absent, and the STAC sink carries forward what
    # the published item already says.
    written: dict[str, gcs.FileMeta] = {}

    data_sinks = [] if unchanged else _data_sinks(topic, con, view, written)
    rc = 0
    data_failed = False
    for name, fn in data_sinks:
        if not _run_sink(topic, name, fn):
            rc = 1
            data_failed = True

    item_changed = False

    def _stac() -> None:
        nonlocal item_changed
        # The hash vouches for the published artifacts, so it only moves when they were all
        # rewritten. Stamping it after a failed data sink would make the next --skip-unchanged run
        # skip the rebuild.
        content_hash = fingerprint.published_hash(topic) if data_failed else fp
        item_changed = sink_stac.write(topic, con, view, metadata=meta, related=related_info,
                                       content_hash=content_hash, file_meta=written)

    if not _run_sink(topic, "stac", _stac):
        rc = 1
    if not skip_refresh and not _refresh_catalog(topic):
        rc = 1
    # A metadata edit changes what the FeatureServer serves too.
    if (data_sinks or item_changed) and changed is not None:
        changed.append(topic.stem)
    return rc


def _ingest(topic: Topic, dry_run: bool = False, skip_refresh: bool = False,
            skip_unchanged: bool = True, changed: list[str] | None = None) -> int:
    backend = _backend()
    # One DuckDB connection streams Postgres scan → transform → materialize (no pyarrow, no
    # Python-held rows; DuckDB spills under its memory cap). The sinks read the materialized table.
    log.info("[%s] reading from Postgres (streaming, single DuckDB)", topic.fqn,
             extra={"topic": topic.fqn})
    con, view = backend.stream_transformed(topic)
    # The sinks read `view` through `con`, so the close waits until they're done. Without it the
    # Cloud Run handler leaks a connection — ATTACHed Postgres + materialized table — per push.
    try:
        return _run_sinks(topic, con, view, backend, dry_run, skip_refresh, skip_unchanged, changed)
    finally:
        con.close()


def ingest_topic(topic: Topic, dry_run: bool = False, skip_refresh: bool = False,
                 skip_unchanged: bool = True, changed: list[str] | None = None) -> int:
    """Ingest a single Topic. Top-level entry point for callers (CLI + service).

    `skip_unchanged` defaults on — an unchanged topic (fingerprint matches the published item) is
    skipped without rebuilding tiles. Pass `skip_unchanged=False` (CLI `--force`) to always rebuild.
    A topic whose data sinks ran or whose item changed is appended to `changed`."""
    try:
        if dry_run:
            return _ingest(topic, dry_run=True, skip_refresh=skip_refresh,
                           skip_unchanged=skip_unchanged)
        # Two publishes for one topic would both rewrite its artifacts. The second waits, then
        # runs; the fingerprint skip makes it cheap when nothing changed.
        with gcs.lease(f"_locks/ingest/{topic.fqn}", ttl_s=LOCK_TTL_S, wait_s=LOCK_WAIT_S):
            return _ingest(topic, skip_refresh=skip_refresh, skip_unchanged=skip_unchanged,
                           changed=changed)
    except Exception as e:
        # Check if it's the "FATAL: no GEOMETRY column found" case (from source.py or transform.py)
        # We can handle non-spatial gracefully by just returning 0 (success, nothing to ingest)
        if "no GEOMETRY column found" in str(e):
            log.warning("[%s] SKIP: non-spatial table (no GEOMETRY column)", topic.fqn,
                        extra={"topic": topic.fqn})
            return 0
        log.exception("[%s] FATAL", topic.fqn, extra={"topic": topic.fqn})
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
             "(no GCS / DuckLake writes; safe smoke against real _current)",
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
    logs.setup()
    skip_unchanged = not args.force
    changed: list[str] = []

    if args.all:
        backend = _backend()
        discovered = backend.discover()
        log.info("discovered %s topics in %s", len(discovered), topics.MART_SCHEMAS)
        rc = 0

        # Skip individual refreshes when running all topics to avoid redundant catalog listings/writes.
        # We will trigger exactly one final refresh at the end instead.
        skip_indiv = True

        if args.parallel:
            from concurrent.futures import ThreadPoolExecutor
            log.info("running ingestion in parallel with %s workers...", args.workers)
            with ThreadPoolExecutor(max_workers=args.workers) as executor:
                def run_one(t: Topic) -> int:
                    return ingest_topic(t, dry_run=args.dry_run, skip_refresh=skip_indiv,
                                        skip_unchanged=skip_unchanged, changed=changed)
                results = list(executor.map(run_one, discovered))
            rc = 1 if any(r != 0 for r in results) else 0
        else:
            for t in discovered:
                rc |= ingest_topic(t, dry_run=args.dry_run, skip_refresh=skip_indiv,
                                   skip_unchanged=skip_unchanged, changed=changed)

        if not args.skip_refresh:
            try:
                log.info("running final STAC catalog refresh...")
                stac.refresh_catalog()
            except Exception:
                log.exception("final stac catalog refresh FAILED")
                rc = 1
        if not args.dry_run:
            jobs.start_topic_thumbs()
            rc |= _invalidate_cdn(changed)
        return rc

    topic = Topic.parse(args.topic)
    rc = ingest_topic(topic, dry_run=args.dry_run, skip_refresh=args.skip_refresh,
                      skip_unchanged=skip_unchanged, changed=changed)
    # Previews in minutes rather than at the nightly thumbnail run, which stays the backstop. Started
    # even after a failed sink, since the layer may still have been republished; the thumbnail job
    # works out whether anything it draws changed.
    if not args.dry_run:
        jobs.start_topic_thumbs([topic.stem])
        rc |= _invalidate_cdn(changed)
    return rc


if __name__ == "__main__":
    sys.exit(main())
