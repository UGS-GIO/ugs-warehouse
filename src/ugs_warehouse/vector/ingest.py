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
import os
import sys
import traceback
from types import ModuleType

from ..core import stac
from . import (
    sink_archive,
    sink_ducklake,
    sink_pmtiles,
    sink_stac,
    source,
    source_postgrest,
    topics,
    transform,
)
from .topics import Topic


def _backend() -> ModuleType:
    """Pick the source backend (env-driven).

    SOURCE_BACKEND=postgrest  -> source_postgrest (HTTP, no DB login needed)
    SOURCE_BACKEND=postgres   -> source (direct Postgres, default)
    """
    return source_postgrest if os.environ.get("SOURCE_BACKEND") == "postgrest" else source


def _ingest(topic: Topic, dry_run: bool = False, skip_refresh: bool = False) -> int:
    backend = _backend()
    label = "PostgREST" if backend is source_postgrest else "Postgres"

    chunk_size = int(os.environ.get("INGEST_CHUNK_SIZE", "5000"))

    # 1. Determine if we should use chunked ingestion
    is_chunked = False
    total_rows = 0
    if hasattr(backend, "get_count") and hasattr(backend, "read_chunk"):
        try:
            total_rows = backend.get_count(topic)
            if total_rows > chunk_size:
                is_chunked = True
        except Exception as e:
            print(f"[{topic.fqn}] get_count failed, falling back to non-chunked: {e}", file=sys.stderr)

    if is_chunked:
        print(f"[{topic.fqn}] reading from {label} in chunks (total rows: {total_rows}, chunk_size: {chunk_size})")

        import math
        import shutil
        import tempfile
        import datetime
        import duckdb
        from ..core import config

        overall_bbox = [float("inf"), float("inf"), float("-inf"), float("-inf")]
        overall_row_count = 0
        has_geom = False

        with tempfile.TemporaryDirectory() as tmp_dir:
            parquet_chunks_dir = os.path.join(tmp_dir, "parquet_chunks")
            os.makedirs(parquet_chunks_dir, exist_ok=True)

            geojsonl_chunks_dir = os.path.join(tmp_dir, "geojsonl_chunks")
            os.makedirs(geojsonl_chunks_dir, exist_ok=True)

            chunk_idx = 0
            for offset in range(0, total_rows, chunk_size):
                limit = min(chunk_size, total_rows - offset)
                print(f"[{topic.fqn}] processing chunk {chunk_idx + 1}/{math.ceil(total_rows / chunk_size)} (offset {offset}, limit {limit})")

                try:
                    arrow_chunk = backend.read_chunk(topic, limit, offset)
                except Exception as e:
                    print(f"[{topic.fqn}] failed to read chunk {chunk_idx}: {e}", file=sys.stderr)
                    traceback.print_exc()
                    return 1

                con, view = transform.run(arrow_chunk)

                count = con.execute(f"SELECT count(*) FROM {view}").fetchone()[0]
                non_null = con.execute(
                    f"SELECT count(*) FROM {view} WHERE geom IS NOT NULL"
                ).fetchone()[0]

                if count == 0:
                    continue

                if non_null > 0:
                    has_geom = True
                    bbox = con.execute(f"""
                        SELECT
                          MIN(ST_XMin(geom)), MIN(ST_YMin(geom)),
                          MAX(ST_XMax(geom)), MAX(ST_YMax(geom))
                        FROM {view}
                        WHERE geom IS NOT NULL
                    """).fetchone()
                    if bbox and all(v is not None for v in bbox):
                        overall_bbox[0] = min(overall_bbox[0], float(bbox[0]))
                        overall_bbox[1] = min(overall_bbox[1], float(bbox[1]))
                        overall_bbox[2] = max(overall_bbox[2], float(bbox[2]))
                        overall_bbox[3] = max(overall_bbox[3], float(bbox[3]))

                overall_row_count += count

                if dry_run:
                    chunk_idx += 1
                    continue

                # Run sink: ducklake (direct append)
                try:
                    sink_ducklake.write(topic, con, view, append=(chunk_idx > 0))
                except Exception as e:
                    print(f"[{topic.fqn}] sink ducklake chunk {chunk_idx} FAILED: {e}", file=sys.stderr)
                    traceback.print_exc()

                # Run sink: archive chunk
                try:
                    chunk_parquet = os.path.join(parquet_chunks_dir, f"chunk_{chunk_idx}.parquet")
                    con.execute(
                        f"COPY (SELECT *, "
                        f"ST_XMin(geom) AS bbox_xmin, ST_YMin(geom) AS bbox_ymin, "
                        f"ST_XMax(geom) AS bbox_xmax, ST_YMax(geom) AS bbox_ymax "
                        f"FROM {view}) TO '{chunk_parquet}' (FORMAT PARQUET, COMPRESSION ZSTD)"
                    )
                except Exception as e:
                    print(f"[{topic.fqn}] sink archive chunk {chunk_idx} FAILED: {e}", file=sys.stderr)
                    traceback.print_exc()

                # Run sink: pmtiles chunk
                try:
                    chunk_geojsonl = os.path.join(geojsonl_chunks_dir, f"chunk_{chunk_idx}.geojsonl")
                    con.execute(
                        f"COPY (SELECT * FROM {view}) TO '{chunk_geojsonl}' "
                        f"(FORMAT GDAL, DRIVER 'GeoJSONSeq')"
                    )
                except Exception as e:
                    print(f"[{topic.fqn}] sink pmtiles chunk {chunk_idx} FAILED: {e}", file=sys.stderr)
                    traceback.print_exc()

                try:
                    con.close()
                except Exception:
                    pass

                chunk_idx += 1

            if not has_geom and overall_row_count > 0:
                print(f"[{topic.fqn}] SKIP: 0/{overall_row_count} rows have geometry", file=sys.stderr)
                return 1

            if dry_run:
                print(f"[{topic.fqn}] DRY-RUN OK (chunked)")
                print(f"  total rows after transform : {overall_row_count}")
                if has_geom:
                    print(f"  overall bbox (4326)        : minx={overall_bbox[0]:.6f} miny={overall_bbox[1]:.6f} "
                          f"maxx={overall_bbox[2]:.6f} maxy={overall_bbox[3]:.6f}")
                return 0

            # Now, merge and upload the chunked outputs
            rc = 0

            # 1. Merge and upload Parquet archive
            local_parquet = os.path.join(tmp_dir, f"{topic.stem}.parquet")
            parquet_files = [f for f in os.listdir(parquet_chunks_dir) if f.endswith(".parquet")]
            if parquet_files:
                try:
                    merge_con = duckdb.connect()
                    merge_con.execute("INSTALL spatial; LOAD spatial;")
                    merge_con.execute(
                        f"COPY (SELECT * FROM read_parquet('{parquet_chunks_dir}/*.parquet')) "
                        f"TO '{local_parquet}' (FORMAT PARQUET, COMPRESSION ZSTD)"
                    )
                    stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d")
                    base = f"{config.ARCHIVE_PREFIX}/{topic.stem}"
                    latest = f"{base}/{topic.stem}.parquet"
                    dated = f"{base}/{topic.stem}_{stamp}.parquet"
                    from ..core import gcs
                    gcs.upload(local_parquet, latest, content_type=sink_archive.PARQUET_MIME, cache_control=gcs.CACHE_MUTABLE)
                    gcs.upload(local_parquet, dated, content_type=sink_archive.PARQUET_MIME, cache_control=gcs.CACHE_IMMUTABLE)
                    print(f"[{topic.fqn}] archive: {config.public_url(latest)} (+ dated {stamp})")
                except Exception as e:
                    print(f"[{topic.fqn}] sink archive merge/upload FAILED: {e}", file=sys.stderr)
                    traceback.print_exc()
                    rc = 1
            else:
                print(f"[{topic.fqn}] sink archive FAILED: no parquet chunks written", file=sys.stderr)
                rc = 1

            # 2. Merge and build PMTiles
            local_geojsonl = os.path.join(tmp_dir, f"{topic.stem}.geojsonl")
            local_pmtiles = os.path.join(tmp_dir, f"{topic.stem}.pmtiles")
            geojsonl_files = [f for f in os.listdir(geojsonl_chunks_dir) if f.endswith(".geojsonl")]
            if geojsonl_files:
                try:
                    with open(local_geojsonl, "wb") as outfile:
                        # Sort by chunk index to keep ordering
                        sorted_files = sorted(geojsonl_files, key=lambda x: int(x.split("_")[1].split(".")[0]))
                        for fname in sorted_files:
                            fpath = os.path.join(geojsonl_chunks_dir, fname)
                            with open(fpath, "rb") as infile:
                                shutil.copyfileobj(infile, outfile)

                    if not shutil.which(sink_pmtiles.TIPPECANOE_BIN):
                        raise RuntimeError(f"{sink_pmtiles.TIPPECANOE_BIN} not on PATH")

                    cmd = [
                        sink_pmtiles.TIPPECANOE_BIN,
                        "-o", local_pmtiles,
                        "-l", topic.stem,
                        "--force",
                        "--drop-densest-as-needed",
                        "--extend-zooms-if-still-dropping",
                        *sink_pmtiles.EXTRA_OPTS,
                        local_geojsonl,
                    ]
                    import subprocess
                    subprocess.run(cmd, check=True)
                    gcs_object = f"{config.PMTILES_PREFIX}/{topic.stem}/{topic.stem}.pmtiles"
                    from ..core import gcs
                    gcs.upload(local_pmtiles, gcs_object, content_type=sink_pmtiles.PMTILES_MIME,
                               cache_control=gcs.CACHE_MUTABLE)
                    print(f"[{topic.fqn}] pmtiles: {config.public_url(gcs_object)}")
                except Exception as e:
                    print(f"[{topic.fqn}] sink pmtiles merge/build/upload FAILED: {e}", file=sys.stderr)
                    traceback.print_exc()
                    rc = 1
            else:
                print(f"[{topic.fqn}] sink pmtiles FAILED: no geojsonl chunks written", file=sys.stderr)
                rc = 1

            # 3. Write STAC
            meta = backend.read_metadata(topic)
            try:
                stac_con = duckdb.connect()
                sink_stac.write(topic, stac_con, "", title=None, description=None, metadata=meta, bbox=overall_bbox, row_count=overall_row_count)
            except Exception as e:
                print(f"[{topic.fqn}] sink stac FAILED: {e}", file=sys.stderr)
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

    else:
        print(f"[{topic.fqn}] reading from {label}")
        arrow_in = backend.read(topic)
        print(f"[{topic.fqn}] {arrow_in.num_rows} rows in, {len(arrow_in.column_names)} columns")

        con, view = transform.run(arrow_in)

        count = con.execute(f"SELECT count(*) FROM {view}").fetchone()[0]
        non_null = con.execute(
            f"SELECT count(*) FROM {view} WHERE geom IS NOT NULL"
        ).fetchone()[0]

        if non_null == 0:
            # No usable geometry — non-spatial table, or the source backend could
            # not read geom (e.g. PostgREST column-level grant hides it). Writing
            # sinks here would emit empty/broken artifacts, so bail before them in
            # both dry-run and real mode.
            cols = [r[0] for r in con.execute(f"DESCRIBE {view}").fetchall()]
            print(f"[{topic.fqn}] SKIP: 0/{count} rows have geometry "
                  f"(non-spatial table, or geom not readable by this backend)",
                  file=sys.stderr)
            print(f"  columns : {cols}", file=sys.stderr)
            return 1

        if dry_run:
            # Validate source + transform without touching any sink.
            cols = [r[0] for r in con.execute(f"DESCRIBE {view}").fetchall()]
            sample = con.execute(f"SELECT * EXCLUDE (geom) FROM {view} LIMIT 1").fetchone()
            bbox = con.execute(f"""
                SELECT
                  MIN(ST_XMin(geom)), MIN(ST_YMin(geom)),
                  MAX(ST_XMax(geom)), MAX(ST_YMax(geom))
                FROM {view}
            """).fetchone()
            print(f"[{topic.fqn}] DRY-RUN OK")
            print(f"  rows after transform : {count} ({non_null} with geometry)")
            print(f"  bbox (4326)          : minx={bbox[0]:.6f} miny={bbox[1]:.6f} "
                  f"maxx={bbox[2]:.6f} maxy={bbox[3]:.6f}")
            print(f"  columns              : {cols}")
            print(f"  sample row (no geom) : {sample}")
            return 0

        # Per-topic descriptive metadata (raw.schema_registry); {} until #171 + grant land.
        meta = backend.read_metadata(topic)

        rc = 0
        for name, fn in [
            ("ducklake", lambda: sink_ducklake.write(topic, con, view)),
            ("archive",  lambda: sink_archive.write(topic, con, view)),
            ("pmtiles",  lambda: sink_pmtiles.build(topic, con, view)),
            ("stac",     lambda: sink_stac.write(topic, con, view, metadata=meta)),
        ]:
            try:
                fn()
            except Exception as e:
                # Per-sink isolation: log + continue, never silent fail.
                print(f"[{topic.fqn}] sink {name} FAILED: {e}", file=sys.stderr)
                traceback.print_exc()
                rc = 1

        # Rebuild the static root STAC catalog so it reflects this item (and all
        # prior ones) — keeps discovery current with no manual regen. Same
        # per-sink isolation: a refresh failure logs + sets rc but never raises.
        if not skip_refresh:
            try:
                stac.refresh_catalog()
            except Exception as e:
                print(f"[{topic.fqn}] stac catalog refresh FAILED: {e}", file=sys.stderr)
                traceback.print_exc()
                rc = 1
        return rc


def ingest_topic(topic: Topic, dry_run: bool = False, skip_refresh: bool = False) -> int:
    """Ingest a single Topic. Top-level entry point for callers (CLI + service)."""
    try:
        return _ingest(topic, dry_run=dry_run, skip_refresh=skip_refresh)
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
    args = ap.parse_args()

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
                    return ingest_topic(t, dry_run=args.dry_run, skip_refresh=skip_indiv)
                results = list(executor.map(run_one, discovered))
            rc = 1 if any(r != 0 for r in results) else 0
        else:
            for t in discovered:
                rc |= ingest_topic(t, dry_run=args.dry_run, skip_refresh=skip_indiv)

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
    return ingest_topic(topic, dry_run=args.dry_run, skip_refresh=args.skip_refresh)


if __name__ == "__main__":
    sys.exit(main())
