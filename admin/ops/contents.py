"""Survey Notes "In this issue" sidecar editor — list issues + read/write the TOC sidecars the
pubs pipeline consumes (`pubs/contents/{SID}.json`).

The thumbs job auto-parses each issue's TOC from the PDF; this lets an operator *correct* a
mis-parse or author one for an issue that didn't parse. A saved sidecar is marked `source:"manual"`
so the auto-parser never clobbers it. Changes land in STAC on the next `pubs-ingest`.

Read-only listing works with the admin SA's storage.objectViewer; saving needs object write.
"""
from __future__ import annotations

import json

# `ugs_warehouse` is imported lazily inside each function: it's on the path at runtime (deploy image),
# not necessarily during `manage.py check`/local dev — same pattern as ops/stac.py.


def list_issues(search: str = "") -> list[dict]:
    """Survey Notes issues (newest first) + each one's TOC sidecar status."""
    from ugs_warehouse.core import gcs
    from ugs_warehouse.pubs import identity, sink_stac, source

    have = {p.rsplit("/", 1)[-1][: -len(".json")].upper()
            for p in gcs.list_paths(identity.PUB_CONTENTS_PREFIX) if p.endswith(".json")}
    q = search.strip().upper()
    rows = []
    for p in source.read_pubs():
        sid = (p.get("series_id") or "").strip()
        if not sid or sink_stac.series_code(sid) != "SNT":
            continue
        up = sid.upper()
        if q and q not in up and q not in (p.get("pub_name") or "").upper():
            continue
        rows.append({
            "id": sid,
            "title": (p.get("pub_name") or "").strip(),
            "year": (p.get("pub_year") or "").strip(),
            "has_sidecar": up in have,
            "pdf": sink_stac.href(p.get("pub_url")),
        })
    rows.sort(key=lambda r: r["id"], reverse=True)
    return rows


def load(sid: str) -> dict:
    """The issue's current sidecar: {entries: [{title, page}], source, pdf}. Empty entries if none."""
    from ugs_warehouse.core import gcs
    from ugs_warehouse.pubs import identity, sink_stac, source

    pub = next((p for p in source.read_pubs()
                if (p.get("series_id") or "").strip().upper() == sid.upper()), {})
    obj = identity.pub_contents_object(sid)
    entries, src = [], None
    if gcs.exists(obj):
        try:
            doc = json.loads(gcs.get_bytes(obj).decode())
            entries = doc.get("contents") or []
            src = doc.get("source")
        except Exception:  # noqa: BLE001
            entries = []
    return {
        "id": sid,
        "title": (pub.get("pub_name") or "").strip(),
        "pdf": sink_stac.href(pub.get("pub_url")),
        "entries": entries,
        "source": src,
    }


def save(sid: str, entries: list[dict]) -> dict:
    """Write the hand-authored sidecar (marked source=manual). Drops blank-title rows, coerces page."""
    from ugs_warehouse.core import gcs
    from ugs_warehouse.pubs import identity

    clean = []
    for e in entries:
        title = (e.get("title") or "").strip()
        if not title:
            continue
        raw = str(e.get("page") or "").strip()
        page = int(raw) if raw.isdigit() else None
        clean.append({"title": title, "page": page})
    gcs.put_bytes(
        json.dumps({"series_id": sid.upper(), "contents": clean, "source": "manual"}, indent=2).encode(),
        identity.pub_contents_object(sid),
        content_type="application/json", cache_control=gcs.CACHE_MUTABLE)
    return {"id": sid, "count": len(clean)}
