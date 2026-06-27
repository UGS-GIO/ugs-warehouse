"""Extract a Survey Notes issue's table of contents ("In this issue") from the PDF itself.

Survey Notes issues carry no PDF outline/bookmarks, but their Contents page is a dot-leader TOC
(``Title .......... 7``). We parse that directly from the publication — no CMS, no hand entry — so
the viewer can list each issue's articles + deep-link the PDF to the right page.

Best-effort + graceful: issues whose layout predates the dot-leader era (or that don't parse) just
return ``[]`` and get no "In this issue" panel. Text is read from the PDF's existing text layer
(present even on UGS's older scanned issues), so no OCR is involved.
"""
from __future__ import annotations

import re
import subprocess

# Title, then dot leaders (>=3 dots, possibly broken by spaces/OCR specks), then a page — a number
# or "back cover". Anchored to the full line so body text doesn't match.
_DOTS = re.compile(r"^(.*?\S)\s*\.\s*\.[.\s]*\.\s*(\d{1,3}|back cover)\s*$", re.IGNORECASE)
# Lines that look like a TOC entry but are masthead/credits noise, not articles.
_SKIP = ("cover ", "design", "issn", "state of utah", "director", "chair", "administration",
         "executive", "governor", "ex officio", "deputy")
_PAGES_SCANNED = 4          # the TOC sits on an early page; scan the first few
_MIN_ENTRIES = 3            # fewer than this → treat as a failed parse, emit nothing


def _clean_title(raw: str) -> str:
    # Collapse whitespace; strip leading/trailing dots, bullets, and stray punctuation/OCR specks.
    t = re.sub(r"\s+", " ", raw).strip()
    return re.sub(r"^[\s.,•·]+|[\s.,•·]+$", "", t)


def _parse(text: str) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for line in text.splitlines():
        m = _DOTS.match(line.strip())
        if not m:
            continue
        title = _clean_title(m.group(1))
        if len(title) < 4 or title.lower().startswith(_SKIP):
            continue
        raw_page = m.group(2)
        page = None if raw_page.lower() == "back cover" else int(raw_page)
        if page is not None and page > 60:        # a real page number, not a stray figure label
            continue
        key = title.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append({"title": title, "page": page})
    return out


def extract(pdf_path: str) -> list[dict]:
    """Best `[{title, page}]` TOC for an issue PDF, or [] when nothing parses cleanly.

    Tries raw + ``-layout`` pdftotext on each of the first pages and keeps whichever page/mode
    yields the most entries (raw usually wins; multi-column layouts sometimes need ``-layout``)."""
    best: list[dict] = []
    for layout in ([], ["-layout"]):
        for pg in range(1, _PAGES_SCANNED + 1):
            try:
                txt = subprocess.run(
                    ["pdftotext", "-f", str(pg), "-l", str(pg), *layout, pdf_path, "-"],
                    capture_output=True, text=True, timeout=60).stdout
            except Exception:  # noqa: BLE001 — a bad page never sinks the issue
                continue
            entries = _parse(txt)
            if len(entries) > len(best):
                best = entries
    return best if len(best) >= _MIN_ENTRIES else []


_ARTICLE_TEXT_CAP = 20_000  # chars per article kept for the search corpus (bounds corpus size)


def article_texts(pdf_path: str, toc: list[dict]) -> list[dict]:
    """Per-article text for the full-text search corpus: slice the PDF by the TOC's page ranges
    (article i = its page .. the next article's page-1). [{title, page, text}], empty if no paged TOC."""
    entries = sorted((e for e in toc if isinstance(e.get("page"), int)), key=lambda e: e["page"])
    out = []
    for i, e in enumerate(entries):
        start = e["page"]
        end = entries[i + 1]["page"] - 1 if i + 1 < len(entries) else None  # last → to end of doc
        cmd = ["pdftotext", "-f", str(start)]
        if end and end >= start:
            cmd += ["-l", str(end)]
        cmd += [pdf_path, "-"]
        try:
            raw = subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout
        except Exception:  # noqa: BLE001
            raw = ""
        text = re.sub(r"\s+", " ", raw).strip()[:_ARTICLE_TEXT_CAP]
        out.append({"title": e["title"], "page": start, "text": text})
    return out
