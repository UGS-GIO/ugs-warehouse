"""Cover thumbnails — a small preview PNG in GCS for ANY pub (Survey Notes covers, report covers, …),
not just harvested maps, so the catalog can show a real thumbnail everywhere.

Cover = the PDF's **first page** (the title/cover page). Fallback only when that PDF is unfetchable —
over the size cap (its `Content-Length` is rejected before any bytes download) or unrenderable: copy
the harvest's COG overview (`geolmap/cogs/{SID}.thumb.png`) if this pub has one. So the scanned-map
plates that bust the cap get the map as a cover instead of nothing, with no multi-hundred-MB download,
while every normal pub keeps its actual title-page cover.

Runs on the harvest image (poppler/pdftoppm). Sharded via CLOUD_RUN_TASK_INDEX/COUNT + skip-existing,
exactly like the COG harvest. Reuses harvest's download / run / structured-logging helpers.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

from ..core import gcs
from . import contents, identity, sink_stac, source
from .harvest import _series_ctx, download, encode_url, hlog, outcome_category, run

THUMB_PX = int(os.environ.get("PUB_THUMB_PX", "400"))
MAX_PDF_MB = int(os.environ.get("PUB_THUMB_MAX_PDF_MB", "150"))


def thumb_object(sid: str) -> str:
    return f"{identity.PUB_THUMB_PREFIX}/{sid.upper()}.png"


def thumb_one(p: dict, force: bool = False) -> str:
    sid = (p.get("series_id") or "").strip()
    if not sid:
        return "skip"
    _series_ctx.set(sid)
    pdf = sink_stac.href(p.get("pub_url"))
    if not pdf or not pdf.lower().split("?")[0].endswith(".pdf"):
        hlog("no PDF — no cover", step="resolve", level="NOTICE", category="expected")
        return "skip:nopdf"

    cover_obj = thumb_object(sid)
    contents_obj = identity.pub_contents_object(sid)
    is_snt = sink_stac.series_code(sid) == "SNT"
    need_cover = force or not gcs.exists(cover_obj)
    # Survey Notes get an "In this issue" sidecar parsed from the PDF. A hand-authored sidecar (saved
    # from the ops console as source=manual) is never clobbered — not even with --force; skip-existing
    # protects an auto-parsed one too, so re-runs only fill gaps.
    need_contents = is_snt and (force or not gcs.exists(contents_obj)) and not _manual_contents(contents_obj)
    if not need_cover and not need_contents:
        hlog("cover + contents already present", step="resolve", level="NOTICE", category="expected")
        return "skip:exists"

    work = tempfile.mkdtemp(prefix=f"t_{sid.replace('/', '_')}_")
    try:
        pdfp = os.path.join(work, "pub.pdf")
        try:
            hlog("downloading PDF", step="download")
            download(encode_url(pdf), pdfp, max_bytes=MAX_PDF_MB * 1024 * 1024)
        except Exception as e:  # noqa: BLE001 — over the size cap (rejected pre-download) or unfetchable
            reason = _reason(e)
            # The cover can still come from the harvest's COG overview (scanned-map plates that bust
            # the cap already have geolmap/cogs/{SID}.thumb.png); the contents parse can't.
            if need_cover and _cog_cover(sid, cover_obj):
                hlog(f"cover from COG overview (PDF unavailable: {reason})", step="result", category="ok")
                return "ok"
            hlog(f"FAIL {reason}", step="result", level="ERROR", category="attention", err=True)
            return f"fail:{type(e).__name__}"

        # PDF in hand — derive whatever's missing, independently.
        if need_cover and not _render_cover(sid, pdfp, work, cover_obj):
            return "fail:norender"
        if need_contents:
            toc = contents.extract(pdfp)
            if toc:
                gcs.put_bytes(
                    json.dumps({"series_id": sid.upper(), "contents": toc}, indent=2).encode(),
                    contents_obj, content_type="application/json", cache_control=gcs.CACHE_MUTABLE)
                hlog(f"OK contents → {len(toc)} entries", step="result", category="ok")
                # Full-text search corpus: per-article text sliced by the TOC page ranges. Ingest
                # aggregates these per-issue sidecars into the one corpus the viewer searches.
                arts = contents.article_texts(pdfp, toc)
                if arts:
                    gcs.put_bytes(
                        json.dumps({"series_id": sid.upper(), "volume": sink_stac.issue_volume(sid),
                                    "title": (p.get("pub_name") or "").strip(),
                                    "pdf": sink_stac.href(p.get("pub_url")), "articles": arts}).encode(),
                        identity.pub_search_object(sid),
                        content_type="application/json", cache_control=gcs.CACHE_MUTABLE)
                    hlog(f"OK search text → {len(arts)} articles", step="result", category="ok")
            else:
                # Pre-dot-leader / atypical layout — graceful; hand-author a sidecar to fix it.
                hlog("no parseable contents (layout)", step="result", level="NOTICE", category="expected")
        return "ok"
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _manual_contents(obj: str) -> bool:
    """True if a hand-authored (source=manual) contents sidecar exists — auto-parse must not touch it."""
    if not gcs.exists(obj):
        return False
    try:
        return json.loads(gcs.get_bytes(obj).decode()).get("source") == "manual"
    except Exception:  # noqa: BLE001
        return False


def _reason(e: Exception) -> str:
    err = (getattr(e, "stderr", "") or str(e)).strip()
    return (err.splitlines()[-1] if err.splitlines() else str(e))[:200]


def _render_cover(sid: str, pdfp: str, work: str, obj: str) -> bool:
    """Render the PDF first page → cover PNG in GCS. On a render miss, fall back to the COG overview.
    Returns False only when there's no cover at all (caller reports fail:norender)."""
    out = os.path.join(work, "cover")
    # First page only, width scaled to THUMB_PX (height proportional). -singlefile → out.png.
    run(["pdftoppm", "-png", "-f", "1", "-l", "1", "-scale-to-x", str(THUMB_PX),
         "-scale-to-y", "-1", "-singlefile", pdfp, out])
    png = out + ".png"
    if not os.path.exists(png):
        if _cog_cover(sid, obj):
            hlog("cover from COG overview (page-1 render failed)", step="result", category="ok")
            return True
        hlog("FAIL no page rendered", step="render", level="ERROR", category="attention", err=True)
        return False
    gcs.upload(png, obj, content_type="image/png", cache_control=gcs.CACHE_IMMUTABLE)
    hlog(f"OK cover → {obj}", step="result", category="ok")
    return True


def _cog_cover(sid: str, obj: str) -> bool:
    """Copy the harvest's COG overview thumbnail (geolmap/cogs/{SID}.thumb.png) to the pub cover
    path when it exists — a tiny PNG, no download. Returns True if a cover was written."""
    cog_thumb = f"{identity.COG_PREFIX}/{sid.upper()}.thumb.png"
    if not gcs.exists(cog_thumb):
        return False
    gcs.put_bytes(gcs.get_bytes(cog_thumb), obj, content_type="image/png",
                  cache_control=gcs.CACHE_IMMUTABLE)
    return True


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Render pub cover thumbnails (PDF page 1) → GCS")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("series_id", nargs="*", default=[], help="Series ID(s) to thumbnail")
    g.add_argument("--all", action="store_true", help="All publications")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--force", action="store_true", help="Re-render even if a cover exists")
    args = ap.parse_args()

    pubs = source.read_pubs()
    if args.all:
        work = pubs
    else:
        want = {s.strip().upper() for s in args.series_id}
        work = [p for p in pubs if (p.get("series_id") or "").strip().upper() in want]
    hlog(f"{len(work)} publications", step="startup")

    n = int(os.environ.get("CLOUD_RUN_TASK_COUNT", "1"))
    i = int(os.environ.get("CLOUD_RUN_TASK_INDEX", "0"))
    if n > 1:
        work = work[i::n]
        hlog(f"shard {i + 1}/{n}: {len(work)} publications", step="shard")
    if args.limit:
        work = work[:args.limit]

    tally = {"ok": 0, "expected": 0, "attention": 0}
    rc = 0
    for p in work:
        res = thumb_one(p, force=args.force)
        tally[outcome_category(res)] += 1
        if res.startswith("fail"):
            rc |= 1
    _series_ctx.set("")
    hlog(f"covers complete: {tally['ok']} ok, {tally['expected']} expected (no-PDF / exists), "
         f"{tally['attention']} need attention", step="summary",
         level="WARNING" if tally["attention"] else "NOTICE")
    return rc


if __name__ == "__main__":
    sys.exit(main())
