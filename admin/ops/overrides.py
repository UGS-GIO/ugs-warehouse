"""Metadata overrides — hand-author a description/title for any STAC item (publication or serving-topic).

Writes `overrides/{ID}.json` marked `source:"manual"`. The warehouse ingest prefers it over source
metadata (see core.stac.manual_override) and it survives reingest, so it's how an operator backfills a
description the source doesn't carry. Changes land in STAC on the next ingest/reingest for that item.

`ugs_warehouse` is imported lazily inside each function (on the path at runtime, not during local
`manage.py check`) — same pattern as ops/stac.py + ops/contents.py.
"""
from __future__ import annotations

import json

_TOPICS = "ugs-serving-topics"


def _override(item_id: str) -> dict:
    from ugs_warehouse.core import gcs, stac
    try:
        return json.loads(gcs.get_bytes(stac.override_object(item_id)))
    except Exception:  # noqa: BLE001 — none / unreadable
        return {}


def _override_ids() -> set[str]:
    """Ids that already have an override sidecar (one prefix list, not a HEAD per row)."""
    from ugs_warehouse.core import config, gcs
    return {p.rsplit("/", 1)[-1][: -len(".json")]
            for p in gcs.list_paths(config.OVERRIDES_PREFIX) if p.endswith(".json")}


def search(q: str = "", limit: int = 50) -> list[dict]:
    """Items (pubs + serving-topics) matching `q` by id or title, with their current source values +
    whether an override already exists. Empty until a query is typed (avoids loading everything)."""
    from ugs_warehouse.core import config, gcs

    ql = (q or "").strip().upper()
    if not ql:
        return []
    have = _override_ids()
    rows: list[dict] = []

    from ugs_warehouse.pubs import source
    for p in source.read_pubs():
        sid = (p.get("series_id") or "").strip()
        if not sid or (ql not in sid.upper() and ql not in (p.get("pub_name") or "").upper()):
            continue
        rows.append({"id": sid, "kind": "publication", "title": (p.get("pub_name") or "").strip(),
                     "description": (p.get("full_citation") or "").strip(), "has_override": sid in have})
        if len(rows) >= limit:
            return rows

    # Serving-topics are few (~dozens) — read each item.json for id + title match.
    for path in gcs.list_paths(f"{config.STAC_PREFIX}/{_TOPICS}/"):
        if not path.endswith(".json") or path.endswith("/collection.json"):
            continue
        stem = path.rsplit("/", 1)[-1][: -len(".json")]
        try:
            item = json.loads(gcs.get_bytes(path).decode())
        except Exception:  # noqa: BLE001
            continue
        props = item.get("properties") or {}
        title = (props.get("title") or "").strip()
        if ql not in stem.upper() and ql not in title.upper():
            continue
        rows.append({"id": stem, "kind": "serving-topic", "title": title,
                     "description": (props.get("description") or "").strip(), "has_override": stem in have})
        if len(rows) >= limit:
            break
    return rows


def load(item_id: str) -> dict:
    """Prefill for the edit form: the current override values (if any) + the source flag."""
    ov = _override(item_id)
    return {"id": item_id, "description": (ov.get("description") or ""),
            "title": (ov.get("title") or ""), "source": ov.get("source")}


def save(item_id: str, description: str = "", title: str = "") -> dict:
    """Write (or clear) the override sidecar. Empty fields are dropped; if nothing remains the sidecar
    is deleted so the item falls back to source metadata."""
    from ugs_warehouse.core import gcs, stac

    doc: dict = {"source": "manual"}
    if (description or "").strip():
        doc["description"] = description.strip()
    if (title or "").strip():
        doc["title"] = title.strip()
    obj = stac.override_object(item_id)
    if len(doc) == 1:  # only the source marker → nothing to override
        gcs.delete(obj)
        return {"id": item_id, "cleared": True}
    gcs.put_bytes(json.dumps(doc, indent=2).encode(), obj,
                  content_type="application/json", cache_control=gcs.CACHE_MUTABLE)
    return {"id": item_id, "fields": [k for k in doc if k != "source"]}
