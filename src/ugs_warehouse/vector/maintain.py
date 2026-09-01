"""DuckLake maintenance — keep the catalog + GCS data path from bloating over time.

DuckLake is append-only: superseded parquet is never removed on its own, and small files accumulate.
DuckDB range-reads every file per scan, so the bill is Class B operations, not stored bytes.

  1. ensure_options        — pin target_file_size so writes AND compaction emit large files
  2. expire_snapshots      — retire snapshots older than the retention window
  3. merge_adjacent_files  — compact small parquet into fewer, larger files
  4. cleanup_old_files     — delete the now-unreferenced parquet from GCS
  5. sweep_orphans          — delete parquet nothing references

Three gotchas, verified on DuckDB 1.5.3 + DuckLake e6a3bd0a:

  * merge_adjacent_files compacts ONE batch per call and returns a row per table, not per file, so
    step 3 loops. The old code called it once and logged count(*) as files compacted.
  * Passing min_file_size/max_file_size makes the merge a no-op. Don't add them back.
  * ducklake_delete_orphaned_files cannot run against gs:// here — it HEADs the data path as if it
    were a directory, which 404s on GCS. Step 5 does the listing/diff itself instead.

Step 5 exists because steps 2 and 4 cannot cover each other: expiring a snapshot drops its files'
catalog references, and cleanup_old_files only walks references — so every file an expiry stranded
is invisible to it and bills forever. That is how this data path reached 111,635 files (273 GB)
against 41 actually referenced (0.1 GB).

Step 3 runs under a budget inside the job timeout so steps 4-5 always run; a run that times out
mid-merge frees nothing.

    python -m ugs_warehouse.vector.maintain [--keep-days N] [--dry-run] [--no-merge] [--report]
"""
from __future__ import annotations

import argparse
import datetime
import os
import time

import duckdb

from . import ducklake

# Retention window (days). Must exceed the data-update interval so the previous version stays
# rollback-able.
DEFAULT_KEEP_DAYS = int(os.environ.get("DUCKLAKE_KEEP_DAYS", "7"))

# Target parquet size, on ingest AND compaction. Persisted in the catalog, so it applies to every
# writer without redeploying them. At 256MB the 38GB enmin_ucrc_wells is ~150 files, not 70,353.
DEFAULT_TARGET_FILE_SIZE = os.environ.get("DUCKLAKE_TARGET_FILE_SIZE", "256MB")

# Wall-clock budget for the compaction loop; job timeout is 7200s, so this leaves room for cleanup.
DEFAULT_BUDGET_SECONDS = int(os.environ.get("DUCKLAKE_MERGE_BUDGET_SECONDS", "4800"))

# Grace before an unreferenced file counts as an orphan. Must exceed the longest ingest, or this
# deletes parquet a run has written but not yet committed.
ORPHAN_GRACE_DAYS = int(os.environ.get("DUCKLAKE_ORPHAN_GRACE_DAYS", "7"))


_UNITS = {"": 1, "B": 1, "KB": 10**3, "MB": 10**6, "GB": 10**9}


def _as_bytes(size: str | None) -> int | None:
    """Normalize '256MB' / '256000000' to a byte count.

    DuckLake stores the option normalized, so the check below must compare bytes — comparing raw
    strings never matches and would rewrite the option every run.
    """
    if size is None:
        return None
    text = str(size).strip().upper()
    for suffix, mult in sorted(_UNITS.items(), key=lambda kv: -len(kv[0])):
        if suffix and text.endswith(suffix):
            text = text[: -len(suffix)].strip()
            break
    else:
        mult = 1
    try:
        return int(float(text) * mult)
    except ValueError:
        return None


def ensure_options(con: duckdb.DuckDBPyConnection, cat: str, *,
                   target_file_size: str = DEFAULT_TARGET_FILE_SIZE,
                   dry_run: bool = False) -> None:
    """Pin the catalog-level write options. Idempotent; persisted in the catalog Postgres."""
    current = dict(con.execute(f"SELECT option_name, value FROM ducklake_options('{cat}')").fetchall())
    have = current.get("target_file_size")
    if _as_bytes(have) is not None and _as_bytes(have) == _as_bytes(target_file_size):
        print(f"[maintain] target_file_size already {have}")
        return
    if dry_run:
        print(f"[maintain] target_file_size {have or 'unset'} -> {target_file_size} (dry-run, not set)")
        return
    con.execute(f"CALL ducklake_set_option('{cat}', 'target_file_size', '{target_file_size}')")
    print(f"[maintain] target_file_size {have or 'unset'} -> {target_file_size}")


def _data_path_parts() -> tuple[str, str] | None:
    """(bucket, prefix) from ducklake.DATA_PATH, or None when it isn't a gs:// path."""
    path = ducklake.DATA_PATH
    for scheme in ("gs://", "gcs://"):
        if path.startswith(scheme):
            bucket, _, prefix = path[len(scheme):].partition("/")
            return bucket, prefix.strip("/") + "/" if prefix.strip("/") else ""
    return None


def _as_object_path(data_file: str, bucket: str, prefix: str) -> str:
    """Normalize a catalog data_file to a bucket-relative object path.

    DuckLake may hand back a full gs:// URI or a path already relative to the data path; both have
    to compare equal to what obstore's listing yields.
    """
    for scheme in ("gs://", "gcs://"):
        if data_file.startswith(scheme):
            _, _, rest = data_file[len(scheme):].partition("/")
            return rest
    stripped = data_file.lstrip("/")
    return stripped if stripped.startswith(prefix) else prefix + stripped


def _referenced_files(con: duckdb.DuckDBPyConnection, cat: str, bucket: str, prefix: str) -> set[str]:
    refs: set[str] = set()
    for schema, table in _tables(con, cat):
        for (data_file,) in con.execute(
            f"SELECT data_file FROM ducklake_list_files('{cat}', '{table}', schema => '{schema}')"
        ).fetchall():
            if data_file:
                refs.add(_as_object_path(data_file, bucket, prefix))
    return refs


def sweep_orphans(con: duckdb.DuckDBPyConnection, cat: str, *,
                  grace_days: int = ORPHAN_GRACE_DAYS, dry_run: bool = False,
                  batch_size: int = 1000, allow_missing_refs: bool = False) -> int:
    """Delete parquet under the data path that no snapshot references.

    Replaces `ducklake_delete_orphaned_files`, which cannot run here: it calls fsspec `modified()`
    on the data path itself, and HEADing a GCS prefix 404s because GCS has no directory objects.
    `cleanup_old_files` cannot cover these either — it walks catalog references, and an expired
    snapshot takes its files' references with it, stranding the files permanently.
    """
    parts = _data_path_parts()
    if parts is None:
        print(f"[maintain] sweep_orphans: skipped (data path is not gs://: {ducklake.DATA_PATH})")
        return 0
    bucket, prefix = parts

    import obstore as obs
    from obstore.store import GCSStore

    referenced = _referenced_files(con, cat, bucket, prefix)
    store = GCSStore(bucket=bucket)
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=grace_days)

    orphans, young, seen = [], 0, set()
    for page in obs.list(store, prefix=prefix):
        for meta in page:
            path = meta["path"]
            if not path.endswith(".parquet"):
                continue
            if path in referenced:
                seen.add(path)
                continue
            if meta["last_modified"] > cutoff:
                young += 1
                continue
            orphans.append(path)

    live = len(seen)
    print(f"[maintain] sweep_orphans: {len(referenced):,} referenced, {live:,} matched live, "
          f"{young:,} inside {grace_days}d grace, {len(orphans):,} orphan(s)")

    # Every referenced file must be found in the listing. One that isn't is either a dangling
    # catalog entry (harmless) or a path we normalized wrong — and in the second case its real
    # object is sitting in `orphans`, so deleting would destroy live data. We cannot tell the two
    # apart from counts, so refuse and name them.
    if referenced - seen:
        missing = sorted(referenced - seen)
        detail = (f"sweep_orphans: {len(missing)} referenced file(s) not found in the listing: "
                  f"{missing[:10]}")
        if not allow_missing_refs:
            raise RuntimeError(
                detail + " — refusing to delete, because a mis-normalized path would be deleted as "
                "an orphan. Confirm each is a dangling catalog entry, then pass --allow-missing-refs."
            )
        print(f"[maintain] WARNING: {detail} — proceeding (--allow-missing-refs)")
    if dry_run:
        for path in orphans[:5]:
            print(f"           would delete {path}")
        return len(orphans)

    for i in range(0, len(orphans), batch_size):
        obs.delete(store, orphans[i:i + batch_size])
    print(f"[maintain] sweep_orphans: deleted {len(orphans):,} file(s)")
    return len(orphans)


def drop_dangling_tables(con: duckdb.DuckDBPyConnection, cat: str, targets: list[str], *,
                         dry_run: bool = False) -> int:
    """Drop DuckLake tables whose referenced parquet is entirely absent from GCS.

    A table pointing at files that no longer exist is unreadable, and its dead reference blocks
    sweep_orphans (which refuses to delete while any reference is unmatched). Dropping it lets the
    nightly sweep run unattended instead of needing --allow-missing-refs forever.

    Refuses if ANY of the table's files still exist, so this cannot be used to delete live data.
    """
    parts = _data_path_parts()
    if parts is None:
        raise SystemExit(f"--drop-table needs a gs:// data path, got {ducklake.DATA_PATH}")
    bucket, prefix = parts

    import obstore as obs
    from obstore.store import GCSStore
    store = GCSStore(bucket=bucket)

    dropped = 0
    for target in targets:
        schema, _, table = target.partition(".")
        if not schema or not table:
            raise SystemExit(f"--drop-table expects SCHEMA.TABLE, got {target!r}")

        present = []
        for (data_file,) in con.execute(
            f"SELECT data_file FROM ducklake_list_files('{cat}', '{table}', schema => '{schema}')"
        ).fetchall():
            path = _as_object_path(data_file, bucket, prefix)
            try:
                obs.head(store, path)
            except Exception:  # noqa: BLE001 — absent is the whole point
                continue
            present.append(path)

        if present:
            raise RuntimeError(
                f"refusing to drop {target}: {len(present)} of its file(s) still exist in GCS "
                f"(e.g. {present[0]}). This flag is only for tables whose data is already gone."
            )

        print(f"[maintain] drop_dangling_tables: {target} references no existing file"
              f"{' (dry-run, not dropped)' if dry_run else ' — dropping'}")
        if not dry_run:
            con.execute(f"DROP TABLE {cat}.{_q(schema)}.{_q(table)}")
        dropped += 1
    return dropped


def _q(name: str) -> str:
    """Double-quote a DuckDB identifier."""
    return '"' + name.replace('"', '""') + '"'


def _tables(con: duckdb.DuckDBPyConnection, cat: str) -> list[tuple[str, str]]:
    return [
        (r[0], r[1]) for r in con.execute(
            "SELECT table_schema, table_name FROM information_schema.tables "
            "WHERE table_catalog = ? ORDER BY 1, 2", [cat]
        ).fetchall()
    ]


def report(con: duckdb.DuckDBPyConnection, cat: str) -> int:
    """Print per-table file counts + bytes. Read-only — rewrites and deletes nothing."""
    opts = con.execute(f"SELECT option_name, value FROM ducklake_options('{cat}')").fetchall()
    print("[report] catalog options:")
    for name, value in opts:
        print(f"           {name} = {value}")

    total_files = total_bytes = 0
    rows = []
    for schema, table in _tables(con, cat):
        n, nbytes = con.execute(
            f"SELECT count(*), coalesce(sum(data_file_size_bytes), 0) "
            f"FROM ducklake_list_files('{cat}', '{table}', schema => '{schema}')"
        ).fetchone()
        rows.append((n, nbytes, f"{schema}.{table}"))
        total_files += n
        total_bytes += nbytes

    print(f"[report] {len(rows)} table(s), {total_files:,} referenced file(s), {total_bytes / 1e9:.1f} GB")
    for n, nbytes, name in sorted(rows, reverse=True)[:20]:
        avg = nbytes / n / 1e6 if n else 0
        print(f"           {n:>8,} files  {nbytes / 1e9:>8.2f} GB  avg {avg:>7.2f} MB  {name}")
    return 0


def compact(con: duckdb.DuckDBPyConnection, cat: str, *,
            budget_seconds: int = DEFAULT_BUDGET_SECONDS) -> tuple[int, int]:
    """Loop merge_adjacent_files until it reports no work, or the budget runs out.

    One call compacts a single batch, so one call cannot drain a backlog.
    """
    deadline = time.monotonic() + budget_seconds
    processed = created = passes = 0
    while True:
        if time.monotonic() >= deadline:
            print(f"[maintain] merge budget ({budget_seconds}s) spent after {passes} pass(es) — "
                  f"stopping so cleanup still runs; the next run resumes from here")
            break
        # No min_file_size/max_file_size — passing them makes this a no-op.
        batch = con.execute(f"SELECT * FROM ducklake_merge_adjacent_files('{cat}')").fetchall()
        batch_processed = sum(r[2] for r in batch)
        if not batch_processed:
            print(f"[maintain] merge_adjacent_files: converged after {passes} pass(es)")
            break
        passes += 1
        processed += batch_processed
        created += sum(r[3] for r in batch)
    print(f"[maintain] merge_adjacent_files: {processed:,} file(s) compacted into {created:,}")
    return processed, created


def maintain(keep_days: int = 7, *, dry_run: bool = False, merge: bool = True,
             budget_seconds: int = DEFAULT_BUDGET_SECONDS,
             target_file_size: str = DEFAULT_TARGET_FILE_SIZE,
             allow_missing_refs: bool = False) -> int:
    """Pin options, expire old snapshots, compact small files, GC parquet. Returns 0 on success."""
    con = duckdb.connect(":memory:")
    cat = ducklake.attach(con)  # registers the obstore fsspec for the GCS data path
    dr = "true" if dry_run else "false"
    tag = " (dry-run)" if dry_run else ""

    # 1. Pin options first, so anything step 3 rewrites lands at the target size.
    ensure_options(con, cat, target_file_size=target_file_size, dry_run=dry_run)

    # 2. Expire old snapshots so their files lose their references and become collectable.
    expired = len(con.execute(
        f"SELECT * FROM ducklake_expire_snapshots('{cat}', "
        f"older_than => now() - INTERVAL '{int(keep_days)} days', dry_run => {dr})"
    ).fetchall())
    print(f"[maintain] expire_snapshots older than {keep_days}d{tag}: {expired} snapshot(s)")

    # 3. Compact. Skipped under --dry-run (it rewrites data) and --no-merge.
    if merge and not dry_run:
        compact(con, cat, budget_seconds=budget_seconds)
    else:
        print(f"[maintain] merge_adjacent_files: skipped{tag or ' (--no-merge)'}")

    # 4. Delete the parquet the expiry + merge left unreferenced.
    deleted = len(con.execute(
        f"SELECT * FROM ducklake_cleanup_old_files('{cat}', "
        f"cleanup_all => true, dry_run => {dr})"
    ).fetchall())
    print(f"[maintain] cleanup_old_files{tag}: {deleted:,} file(s) removed from GCS")

    # 5. Delete parquet nothing references — stranded when expiry dropped its snapshot's refs, or
    #    left by an ingest that died before committing. cleanup_old_files cannot see either.
    sweep_orphans(con, cat, dry_run=dry_run, allow_missing_refs=allow_missing_refs)

    con.close()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="DuckLake maintenance (expire + compact + GC).")
    ap.add_argument("--keep-days", type=int, default=DEFAULT_KEEP_DAYS,
                    help=f"retain snapshots newer than N days (default {DEFAULT_KEEP_DAYS}, "
                         "or $DUCKLAKE_KEEP_DAYS)")
    ap.add_argument("--dry-run", action="store_true", help="report only; delete/rewrite nothing")
    ap.add_argument("--no-merge", action="store_true", help="skip the file-compaction step")
    ap.add_argument("--report", action="store_true",
                    help="print per-table file counts and exit; changes nothing")
    ap.add_argument("--budget-seconds", type=int, default=DEFAULT_BUDGET_SECONDS,
                    help=f"wall-clock cap on the compaction loop (default {DEFAULT_BUDGET_SECONDS}, "
                         "or $DUCKLAKE_MERGE_BUDGET_SECONDS)")
    ap.add_argument("--drop-table", action="append", metavar="SCHEMA.TABLE", default=[],
                    help="drop a DuckLake table whose parquet is already gone from GCS, then exit "
                         "(refuses if any of its files still exist). Repeatable.")
    ap.add_argument("--allow-missing-refs", action="store_true",
                    help="proceed when a referenced file is absent from the listing (confirm each "
                         "is a dangling catalog entry first — see sweep_orphans)")
    ap.add_argument("--target-file-size", default=DEFAULT_TARGET_FILE_SIZE,
                    help=f"parquet target size pinned in the catalog (default {DEFAULT_TARGET_FILE_SIZE}, "
                         "or $DUCKLAKE_TARGET_FILE_SIZE)")
    args = ap.parse_args()

    if args.report:
        con = duckdb.connect(":memory:")
        try:
            return report(con, ducklake.attach(con))
        finally:
            con.close()

    if args.drop_table:
        con = duckdb.connect(":memory:")
        try:
            drop_dangling_tables(con, ducklake.attach(con), args.drop_table, dry_run=args.dry_run)
            return 0
        finally:
            con.close()

    return maintain(args.keep_days, dry_run=args.dry_run, merge=not args.no_merge,
                    budget_seconds=args.budget_seconds, target_file_size=args.target_file_size,
                    allow_missing_refs=args.allow_missing_refs)


if __name__ == "__main__":
    raise SystemExit(main())
