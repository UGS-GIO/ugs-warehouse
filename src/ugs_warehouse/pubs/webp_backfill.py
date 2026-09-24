"""One-time backfill for #372: a WebP next to every PNG preview already published, so switching the
producers to WebP neither re-downloads every PDF (the cover job renders only a cover it can't find)
nor leaves items pointing at images that don't exist yet.

Plate thumbnails (`{COG_PREFIX}/{SID}.thumb.png`, 700 px wide) become the map's catalog thumbnail
and the 3D viewer's sheet; covers (`{PUB_THUMB_PREFIX}/{SID}.png`) become WebP covers. Additive: it
writes only WebPs that don't exist yet and never touches a PNG.

    python -m ugs_warehouse.pubs.webp_backfill            # count what would be written
    python -m ugs_warehouse.pubs.webp_backfill --apply    # write it (sharded by CLOUD_RUN_TASK_*)
    python -m ugs_warehouse.pubs.webp_backfill --check    # exit 1 while any PNG still lacks its WebP
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

from ..core import config, gcs
from . import identity, webp

Targets = list[tuple[str, int | None]]  # (WebP object, fit)


def _targets(listed: set[str]) -> dict[str, Targets]:
    """{PNG: [(WebP object, fit), ...]} for every PNG preview directly under the two prefixes."""
    out: dict[str, Targets] = {}
    for prefix, suffix in ((identity.COG_PREFIX, identity.PNG_THUMB_SUFFIX),
                           (identity.PUB_THUMB_PREFIX, identity.PNG_COVER_SUFFIX)):
        root = f"{prefix}/"
        for path in sorted(p for p in listed if p.startswith(root)):
            name = path[len(root):]
            if "/" in name or not name.endswith(suffix):
                continue
            sid = name[: -len(suffix)]
            if suffix == identity.PNG_THUMB_SUFFIX:
                out[path] = webp.plate_previews(identity.Pub.parse(sid))
            else:
                out[path] = [(identity.pub_cover_object(sid), webp.CATALOG_PX)]
    return out


def _convert(src: str, targets: Targets) -> str | None:
    """Write `targets` from the PNG at `src`, downloaded once. Returns an error line, or None."""
    work = tempfile.mkdtemp(prefix="webp_")
    try:
        png = os.path.join(work, "src.png")
        with open(png, "wb") as f:
            f.write(gcs.get_bytes(src))
        for obj, fit in targets:
            out = os.path.join(work, obj.rsplit("/", 1)[-1])
            webp.encode(png, out, fit=fit)
            gcs.upload(out, obj, content_type=config.WEBP_MIME, cache_control=gcs.CACHE_IMMUTABLE)
        return None
    except Exception as e:  # noqa: BLE001 (reported and fails the run; the other PNGs still convert)
        detail = str(e).strip().splitlines()
        return f"{src}: {type(e).__name__}: {detail[-1] if detail else ''}"
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="Write a WebP next to every PNG preview already published")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="write the missing WebPs (default: count them)")
    mode.add_argument("--check", action="store_true", help="exit 1 while any PNG still lacks its WebP")
    ap.add_argument("--workers", type=int, default=16)
    args = ap.parse_args()

    listed = {p for prefix in (identity.COG_PREFIX, identity.PUB_THUMB_PREFIX)
              for p in gcs.list_paths(f"{prefix}/")}
    targets = _targets(listed)
    todo = {src: missing for src, wanted in targets.items()
            if (missing := [(obj, fit) for obj, fit in wanted if obj not in listed])}
    if args.check:
        print(f"[webp] {sum(len(t) for t in todo.values())} WebP preview(s) still missing")
        for src in list(todo)[:20]:
            print(f"  {src} -> {', '.join(obj for obj, _ in todo[src])}")
        return 1 if todo else 0

    # Sharded by source PNG, so a plate's two images come from one download, and over every PNG
    # rather than what's left, so a shard's share doesn't shift as the others finish.
    srcs = sorted(targets)
    n = int(os.environ.get("CLOUD_RUN_TASK_COUNT", "1"))
    i = int(os.environ.get("CLOUD_RUN_TASK_INDEX", "0"))
    if n > 1:
        srcs = srcs[i::n]
    srcs = [s for s in srcs if s in todo]
    print(f"[webp] {len(srcs)} PNG(s) → {sum(len(todo[s]) for s in srcs)} WebP(s)"
          + ("" if args.apply else " (dry run; --apply writes them)"))
    if not args.apply:
        return 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        failures = [err for err in ex.map(lambda s: _convert(s, todo[s]), srcs) if err]
    for err in failures:
        print(f"[webp] FAIL {err}", file=sys.stderr)
    print(f"[webp] converted {len(srcs) - len(failures)} of {len(srcs)} PNG(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
