"""DuckLake maintenance — keep the catalog + GCS data path from bloating over time.

DuckLake is append-only: every ingest writes new parquet chunks + a new snapshot, and superseded
files are NEVER removed on their own (that snapshot model is what makes time-travel work). Left
unmaintained the data path grows without bound and each table accumulates many small parquet files,
which slows scans + query planning. This runs the three housekeeping calls in the ONLY correct order:

  1. expire_snapshots      — retire snapshots older than the retention window (drops their refs)
  2. merge_adjacent_files  — compact each table's small parquet into fewer, larger files
  3. cleanup_old_files     — physically delete the now-unreferenced parquet from GCS

Reuses ducklake.attach(), so the delete/rewrite IO routes through the same obstore/fsspec GCS
filesystem the writes use (httpfs/HMAC is blocked by org policy — see ducklake.py).

    python -m ugs_warehouse.vector.maintain [--keep-days N] [--dry-run] [--no-merge]

Deployed as the `ugs-warehouse-ducklake-maintain` Cloud Run job; run periodically (weekly is plenty).
"""
from __future__ import annotations

import argparse

import duckdb

from . import ducklake


def maintain(keep_days: int = 7, *, dry_run: bool = False, merge: bool = True) -> int:
    """Expire old snapshots, compact small files, GC orphaned parquet. Returns 0 on success."""
    con = duckdb.connect(":memory:")
    cat = ducklake.attach(con)  # registers the obstore fsspec for the GCS data path
    dr = "true" if dry_run else "false"
    tag = " (dry-run)" if dry_run else ""

    # 1. Expire snapshots older than the retention window. Keeps recent history for rollback /
    #    time-travel; everything older loses its references so its files become collectable.
    expired = con.execute(
        f"SELECT count(*) FROM ducklake_expire_snapshots('{cat}', "
        f"older_than => now() - INTERVAL '{int(keep_days)} days', dry_run => {dr})"
    ).fetchone()[0]
    print(f"[maintain] expire_snapshots older than {keep_days}d{tag}: {expired} snapshot(s)")

    # 2. Compact adjacent small parquet files per table → fewer, larger files (faster scans).
    #    Skipped under --dry-run (it rewrites data) and --no-merge.
    if merge and not dry_run:
        merged = con.execute(f"SELECT count(*) FROM ducklake_merge_adjacent_files('{cat}')").fetchone()[0]
        print(f"[maintain] merge_adjacent_files: {merged} file group(s) compacted")
    else:
        print(f"[maintain] merge_adjacent_files: skipped{tag or ' (--no-merge)'}")

    # 3. Delete the parquet the expiry (+ merge) left unreferenced. cleanup_all removes every
    #    now-orphaned file, not just those past a grace window.
    deleted = con.execute(
        f"SELECT count(*) FROM ducklake_cleanup_old_files('{cat}', "
        f"cleanup_all => true, dry_run => {dr})"
    ).fetchone()[0]
    print(f"[maintain] cleanup_old_files{tag}: {deleted} file(s) removed from GCS")

    con.close()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="DuckLake maintenance (expire + compact + GC).")
    ap.add_argument("--keep-days", type=int, default=7,
                    help="retain snapshots newer than N days (default 7)")
    ap.add_argument("--dry-run", action="store_true", help="report only; delete/rewrite nothing")
    ap.add_argument("--no-merge", action="store_true", help="skip the file-compaction step")
    args = ap.parse_args()
    return maintain(args.keep_days, dry_run=args.dry_run, merge=not args.no_merge)


if __name__ == "__main__":
    raise SystemExit(main())
