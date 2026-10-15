"""The DOIs UGS has registered, from Crossref.

A publication carries `sci:doi` and a `cite-as` link only when its DOI (`10.34191/<series id>`)
is registered; the rest of the ids do not resolve at doi.org. Crossref is the registry of truth.
`registered` fetches the set once per ingest run and keeps a snapshot in the bucket, which a
later run uses when Crossref cannot be reached.
"""
from __future__ import annotations

import datetime
import json
import sys

import requests

from ..core import gcs
from . import identity

DOI_PREFIX = "10.34191"
CROSSREF_WORKS_URL = f"https://api.crossref.org/prefixes/{DOI_PREFIX}/works"
PAGE_ROWS = 1000
TIMEOUT_S = 30
USER_AGENT = "ugs-warehouse"


def doi_of(series_id: str) -> str:
    """The DOI a publication would have, lower-cased as Crossref lists it."""
    return f"{DOI_PREFIX}/{series_id}".lower()


def _fetch() -> frozenset[str]:
    """Every DOI under UGS's prefix, paging with Crossref's cursor. Raises on an HTTP error, an
    unexpected body, or an empty registry."""
    dois: set[str] = set()
    cursor = "*"
    while True:
        r = requests.get(CROSSREF_WORKS_URL, timeout=TIMEOUT_S, headers={"User-Agent": USER_AGENT},
                         params={"rows": PAGE_ROWS, "select": "DOI", "cursor": cursor})
        r.raise_for_status()
        message = r.json().get("message")
        items = message.get("items") if isinstance(message, dict) else None
        if not isinstance(items, list):
            raise ValueError(f"Crossref response has no message.items: {str(r.text)[:200]}")
        for it in items:
            if not isinstance(it, dict) or not isinstance(it.get("DOI"), str):
                raise ValueError(f"Crossref item without a DOI: {it!r}")
            dois.add(it["DOI"].strip().lower())
        cursor = message.get("next-cursor")
        if not items or not cursor:
            break
    if not dois:
        raise ValueError("Crossref lists no DOIs under the UGS prefix")
    return frozenset(dois)


def _read_snapshot() -> tuple[frozenset[str], str] | None:
    """`(DOIs, fetched_at)` from the snapshot, or None when there is none."""
    try:
        doc = json.loads(gcs.get_bytes(identity.PUB_DOI_REGISTRY_OBJECT).decode())
    except FileNotFoundError:
        return None
    dois, fetched = doc.get("dois"), doc.get("fetched_at")
    if not isinstance(dois, list) or not isinstance(fetched, str):
        raise ValueError(f"{identity.PUB_DOI_REGISTRY_OBJECT} is not a DOI snapshot")
    return frozenset(str(d).lower() for d in dois), fetched


def _write_snapshot(dois: frozenset[str]) -> None:
    doc = {"fetched_at": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
           "dois": sorted(dois)}
    gcs.put_bytes(json.dumps(doc).encode(), identity.PUB_DOI_REGISTRY_OBJECT,
                  content_type="application/json", cache_control=gcs.CACHE_MUTABLE)


def registered() -> frozenset[str]:
    """The registered DOIs, lower-case. A successful fetch rewrites the snapshot. A failed one uses
    the snapshot and warns with the error and the snapshot's age; with no snapshot it raises."""
    try:
        dois = _fetch()
    except Exception as e:  # noqa: BLE001 (any fetch failure falls back to the snapshot, loudly)
        reason = f"{type(e).__name__}: {e}"
        snap = _read_snapshot()
        if snap is None:
            raise RuntimeError(f"DOI registry fetch failed ({reason}) and there is no snapshot at "
                               f"{identity.PUB_DOI_REGISTRY_OBJECT}") from e
        dois, fetched = snap
        age = datetime.datetime.now(datetime.UTC) - datetime.datetime.fromisoformat(fetched)
        print(f"[pubs] WARNING: DOI registry fetch failed ({reason}); using the snapshot from "
              f"{fetched} ({age.days} days old)", file=sys.stderr)
        return dois
    _write_snapshot(dois)
    return dois
