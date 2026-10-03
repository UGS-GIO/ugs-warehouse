"""Retire a serving topic — one entrypoint for every artifact an ingest published.

    python -m ugs_warehouse.vector.retire --topic hazards.hazards_qfaults_current [--dry-run]

Order is not cosmetic. The DuckLake table goes first: `maintain.drop_table` reads the parquet the
catalog references, then deletes it, and a dropped table cannot be listed. Deleting the GCS
artifacts first would leave the table's own files behind under the DuckLake data path, where only
the nightly sweep would ever find them.

What stays behind on purpose:

  * the metadata override (`OVERRIDES_PREFIX/<stem>.json`) — hand-written, not reproducible from
    source, so a re-ingest of the same topic recovers its curation. `--purge-overrides` drops it.
  * the ugs-styles binding — published by a neighbour repo, read-only here. A live binding is
    reported for an operator to remove there.

The catalog is derived from the objects, so `refresh_catalog()` at the end is what makes the topic
disappear from `catalog.json` and its schema collection. There is no separate item database and no
tombstone: absent from GCS is absent from the catalog.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

import duckdb

from ..core import config, gcs, stac, styles
from . import ducklake, maintain
from .sink_stac import CATALOG
from .topics import Topic

# Repo root, for the reference scan: .../src/ugs_warehouse/vector/retire.py
_REPO_ROOT = Path(__file__).resolve().parents[3]


def artifact_paths(topic: Topic) -> list[str]:
    """Every published object for the topic. Trailing slashes keep `<stem>/` from also matching a
    longer stem that starts with it."""
    stem = topic.stem
    prefixes = (
        f"{config.ARCHIVE_PREFIX}/{stem}/",   # latest pointer + every dated parquet
        f"{config.PMTILES_PREFIX}/{stem}/",
        f"{config.THUMBS_PREFIX}/{stem}/",    # png + its style-hash sidecar
        f"{config.STAC_PREFIX}/{CATALOG}/{topic.schema}/{stem}/",  # item.json + iso.xml
    )
    return [path for prefix in prefixes for path in gcs.list_paths(prefix)]


def references(stem: str) -> list[str]:
    """Tracked files that mention the topic, tests excluded.

    ADVISORY, never a gate. Consumers bind layers by STAC item id and nothing declares that binding
    yet (#238), so this over-reports — a stem used as a docstring example reads the same as a real
    binding — while missing the consumer that matters most, which lives in another repo.
    """
    try:
        found = subprocess.run(  # noqa: S603 — fixed argv, stem is an identifier (topics.IDENT_RE)
            ["git", "-C", str(_REPO_ROOT), "grep", "-Fl", "--", stem, ":!tests", ":!*.test.*"],
            capture_output=True, text=True, timeout=60, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []  # not a checkout, or no git
    return [line for line in found.stdout.splitlines() if line]


def confirmed(topic: Topic) -> bool:
    """Make the operator type the topic back. The only gate that holds: no inventory can prove a
    consumer is absent, and a scan noisy enough to fire on every retire teaches people to skip it."""
    if not sys.stdin.isatty():
        print("[retire] refusing: not a terminal, and --yes was not passed", file=sys.stderr)
        return False
    return input(f"[retire] type {topic.fqn} to retire it: ").strip() == topic.fqn


def retire(topic: Topic, *, dry_run: bool = False, assume_yes: bool = False,
           purge_overrides: bool = False) -> int:
    stem = topic.stem
    tag = " (dry-run)" if dry_run else ""
    print(f"[retire] {topic.fqn} -> stem {stem!r}{tag}")
    # Set by the ops console from the operator's IAP identity. The run's own log stream is the audit
    # record — the console has no durable store to write one to.
    requested_by = os.environ.get("RETIRE_REQUESTED_BY")
    if requested_by:
        print(f"[retire] requested by {requested_by}")

    refs = references(stem)
    if refs:
        print(f"[retire] {len(refs)} tracked file(s) mention {stem!r} (examples included, check them):")
        for path in refs:
            print(f"           {path}")

    binding = styles.entry_for(stem)
    if binding:
        print(f"[retire] ugs-styles still binds {stem!r} ({binding.get('path') or 'no path'}) — "
              "remove it in that repo; this tool cannot write the manifest")

    if not dry_run and not assume_yes and not confirmed(topic):
        print("[retire] aborted, nothing changed", file=sys.stderr)
        return 2

    con = duckdb.connect(":memory:")
    try:
        maintain.drop_table(con, ducklake.attach(con), f"{topic.schema}.{stem}", dry_run=dry_run)
    finally:
        con.close()

    paths = artifact_paths(topic)
    override = stac.override_object(stem)
    if purge_overrides and gcs.exists(override):
        paths.append(override)
    elif gcs.exists(override):
        print(f"[retire] keeping {override} (re-ingest recovers the curation; --purge-overrides "
              "to delete it)")

    for path in paths:
        print(f"           {'would delete' if dry_run else 'delete'} {path}")
        if not dry_run:
            gcs.delete(path)
    print(f"[retire] {len(paths)} object(s){tag}")

    if dry_run:
        print("[retire] dry-run — catalog not refreshed, nothing written")
        return 0

    stac.refresh_catalog()
    print(f"[retire] {topic.fqn} retired")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Retire a serving topic (DuckLake + artifacts + catalog)")
    ap.add_argument("--topic", required=True,
                    help="dotted form: schema.layer_current (e.g. hazards.hazards_qfaults_current)")
    ap.add_argument("--dry-run", action="store_true",
                    help="report the table, the objects and the consumers; write nothing")
    ap.add_argument("--yes", action="store_true",
                    help="skip the typed confirmation (for automation)")
    ap.add_argument("--purge-overrides", action="store_true",
                    help="also delete the hand-authored metadata override (kept by default)")
    args = ap.parse_args()

    try:
        topic = Topic.parse(args.topic)
    except ValueError as e:
        print(f"[retire] {e}", file=sys.stderr)
        return 2
    return retire(topic, dry_run=args.dry_run, assume_yes=args.yes,
                  purge_overrides=args.purge_overrides)


if __name__ == "__main__":
    raise SystemExit(main())
