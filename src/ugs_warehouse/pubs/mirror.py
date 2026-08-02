"""Copy publication source files (PDFs, plate/GIS zips, tables) into the warehouse bucket.

The catalog has always published *derived* artifacts — COG, units GeoParquet, thumbnails — while
the publications themselves stayed on `ugspub.nr.utah.gov`. That host works, but it isn't ours, it
sends no CORS header (so browser code can navigate to a PDF and never read its bytes), and an item
whose primary asset lives elsewhere is linked, not archived. See #120.

SELECTIVE by default: only pubs that already have a harvested COG — the map pubs the viewer
actually renders. That's the ~20 GB slice of a ~200 GB full mirror. `--all-pubs` takes everything.

Layout is path-preserving (`identity.pub_file_object`): the legacy URL's path under
`/publications/` becomes the object path under `pubs/files/`. So a plain listing of that prefix
tells `ingest` exactly which assets it holds — no manifest to keep in sync, and re-running after a
partial failure just fills the gaps.

    python -m ugs_warehouse.pubs.mirror --dry-run       # what would copy, and how many bytes
    python -m ugs_warehouse.pubs.mirror                 # COG-bearing pubs (the default slice)
    python -m ugs_warehouse.pubs.mirror --series-id OFR-760
    python -m ugs_warehouse.pubs.mirror --all-pubs      # every pub, the full mirror

Env: MIRROR_MAX_MB (per-file cap, 0 = none), MIRROR_WORKERS.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

from ..core import gcs
from . import identity, sink_stac, source

MAX_MB = int(os.environ.get("MIRROR_MAX_MB", "0"))
# Modest: these are big files off one IIS host, and the point is a background backfill, not a
# thundering herd against the site the public also uses.
WORKERS = int(os.environ.get("MIRROR_WORKERS", "4"))


def cog_series_ids() -> set[str]:
    """Series ids with a harvested COG — the map pubs, and the default mirror slice."""
    suffix = ".cog.tif"
    return {path.rsplit("/", 1)[-1][: -len(suffix)].upper()
            for path in gcs.list_paths(identity.COG_PREFIX) if path.endswith(suffix)}


def plan(pubs: list[dict], attachments: list[dict], sids: set[str] | None) -> list[tuple[str, str, str]]:
    """[(series_id, source_url, object_path)] for the files to mirror.

    Pure — no network, no bucket. `sids` limits to those series (None = every pub). Files already
    in the bucket are NOT filtered here; `run()` skips those, so a plan doubles as an inventory.
    """
    by_sid: dict[str, list[str]] = {}
    for p in pubs:
        sid = (p.get("series_id") or "").strip().upper()
        if sid and (sids is None or sid in sids):
            by_sid.setdefault(sid, []).append(sink_stac.href(p.get("pub_url")) or "")
    for a in attachments:
        sid = (a.get("series_id") or "").strip().upper()
        if sid in by_sid:
            by_sid[sid].append(sink_stac.href(a.get("pub_url")) or "")

    out: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for sid in sorted(by_sid):
        for url in by_sid[sid]:
            obj = identity.pub_file_object(url)
            # One object per URL: pubs share files (a quad's GeoTIFF zip is attached to several),
            # and mirroring the same bytes twice would be a wasted download either way.
            if obj and obj not in seen:
                seen.add(obj)
                out.append((sid, url, obj))
    return out


def copy_one(url: str, obj: str, *, max_bytes: int | None) -> int:
    """Download one file and put it in the bucket. Returns bytes copied."""
    from .harvest import download, encode_url  # local: pulls requests + the retrying session

    with tempfile.TemporaryDirectory() as work:
        dest = os.path.join(work, obj.rsplit("/", 1)[-1] or "file")
        download(encode_url(url), dest, max_bytes=max_bytes)
        size = os.path.getsize(dest)
        # Pubs are immutable once published, so the copy can cache forever.
        gcs.upload(dest, obj, content_type=sink_stac.media_type(url),
                   cache_control=gcs.CACHE_IMMUTABLE)
        return size


def run(*, all_pubs: bool = False, series_id: str | None = None, limit: int | None = None,
        dry_run: bool = False, force: bool = False) -> int:
    """Mirror the selected slice. Returns the number of files copied (or planned, when dry-run)."""
    print(f"[mirror] metadata source: {source.source_name()}")
    if series_id:
        sids: set[str] | None = {series_id.strip().upper()}
    elif all_pubs:
        sids = None
    else:
        sids = cog_series_ids()
        print(f"[mirror] {len(sids)} pubs have a harvested COG")

    todo = plan(source.read_pubs(), source.read_attachments(), sids)
    if not force:
        have = set(gcs.list_paths(identity.PUB_FILES_PREFIX))
        skipped = sum(1 for _, _, obj in todo if obj in have)
        todo = [t for t in todo if t[2] not in have]
        print(f"[mirror] {skipped} already mirrored, {len(todo)} to copy")
    if limit:
        todo = todo[:limit]
    if dry_run:
        for sid, url, obj in todo:
            print(f"[mirror] would copy {sid}: {url} -> {obj}")
        print(f"[mirror] dry run: {len(todo)} files")
        return len(todo)

    max_bytes = MAX_MB * 1024 * 1024 if MAX_MB else None
    done = failed = total_bytes = 0

    def one(t: tuple[str, str, str]) -> int:
        sid, url, obj = t
        try:
            n = copy_one(url, obj, max_bytes=max_bytes)
            print(f"[mirror] {sid}: {obj} ({n} bytes)", flush=True)
            return n
        except Exception as e:  # noqa: BLE001 — one bad file must not sink the backfill
            print(f"[mirror] FAIL {sid}: {url} -> {e}", file=sys.stderr, flush=True)
            return -1

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for n in ex.map(one, todo):
            if n < 0:
                failed += 1
            else:
                done += 1
                total_bytes += n
    print(f"[mirror] copied {done} files ({total_bytes / 1e9:.2f} GB), {failed} failed")
    # Assets only repoint at the CDN once the catalog is rebuilt — the copy alone changes nothing.
    if done:
        print("[mirror] run `python -m ugs_warehouse.pubs.ingest` to repoint the STAC assets")
    return done


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--all-pubs", action="store_true",
                    help="mirror every pub, not just those with a harvested COG")
    ap.add_argument("--series-id", help="mirror one publication (e.g. OFR-760)")
    ap.add_argument("--limit", type=int, help="stop after N files (smoke test)")
    ap.add_argument("--dry-run", action="store_true", help="list what would copy, copy nothing")
    ap.add_argument("--force", action="store_true", help="re-copy files already in the bucket")
    a = ap.parse_args()
    run(all_pubs=a.all_pubs, series_id=a.series_id, limit=a.limit,
        dry_run=a.dry_run, force=a.force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
