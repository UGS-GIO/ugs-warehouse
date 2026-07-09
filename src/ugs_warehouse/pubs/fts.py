"""Build the unified full-text-search database: a DuckDB file with a BM25 FTS index over EVERYTHING
the viewer searches — publication full text, Survey Notes articles, and catalog items (maps/vector
serving-layers + pub COGs).

Three grains land in one `docs` table, discriminated by `kind`:
  - `pub`     — one row per publication, `body` = the whole-document text (`pubs/fulltext/{SID}.txt`).
  - `article` — one row per Survey Notes article, from `pubs/search/corpus.json` (title + sliced text).
  - `item`    — one row per STAC item, from each collection's `items.json` (title + metadata + keywords).

`docs.id` is the FTS key and MUST be unique, so it is kind-prefixed (`pub:RI-232`, `item:RI-232`,
`article:SV-52-1#3`) — a pub and its COG item would otherwise collide. The natural id + linking fields
(sid/page/pdf for pubs+articles, coll_id/item_id for items) ride in their own columns so the viewer can
render + deep-link each hit. Uploaded to the CDN; the viewer queries it client-side via duckdb-wasm
range reads (no server). Run as its own Cloud Run job after the pubs ingest (which builds corpus.json)
and the vector/pubs STAC ingest (which writes items.json).

    python -m ugs_warehouse.pubs.fts
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor

from ..core import config, gcs
from . import identity, sink_stac, source

# Object the .duckdb lands at on the CDN; the viewer ATTACHes this URL.
FTS_OBJECT = os.environ.get("PUB_FTS_OBJECT", "pubs/search/pubs-fts.duckdb")

# One flat row shape across all three grains; absent fields are NULL per kind.
COLS = ("id", "kind", "title", "body", "keywords", "series", "year", "pdf",
        "sid", "page", "volume", "issue", "topic", "coll_id", "item_id")

# STAC item properties folded into the searchable `body` (the compact items.json carries these).
_ITEM_BODY_PROPS = ("ugs:series", "ugs:pub_type", "ugs:topic", "ugs:scale",
                    "ugs:author", "ugs:county", "ugs:layer")


def _row(**kw) -> tuple:
    """Build a full-width row tuple from kind-specific fields (missing → NULL)."""
    return tuple(kw.get(c) for c in COLS)


def _pub_rows() -> list[tuple]:
    """One row per publication: whole-document body text joined to pub metadata."""
    meta = {(p.get("series_id") or "").strip().upper(): p for p in source.read_pubs()}
    paths = [p for p in gcs.list_paths(identity.PUB_FULLTEXT_PREFIX) if p.endswith(".txt")]
    print(f"[fts] {len(paths)} fulltext docs; {len(meta)} pubs in metadata")

    def load(path: str) -> tuple | None:
        sid = path.rsplit("/", 1)[-1][:-len(".txt")].upper()
        try:
            body = gcs.get_bytes(path).decode("utf-8", "ignore")
        except Exception:  # noqa: BLE001
            return None
        if not body.strip():
            return None
        p = meta.get(sid, {})
        return _row(id=f"pub:{sid}", kind="pub", title=(p.get("pub_name") or "").strip(),
                    body=body, series=(p.get("series") or "").strip(),
                    year=str(p.get("pub_year") or "").strip(),
                    pdf=sink_stac.href(p.get("pub_url")), sid=sid)

    with ThreadPoolExecutor(max_workers=16) as ex:
        return [r for r in ex.map(load, paths) if r is not None]


def _article_rows() -> list[tuple]:
    """One row per Survey Notes article, from the pre-built corpus.json."""
    obj = f"{identity.PUB_SEARCH_PREFIX}/corpus.json"
    try:
        corpus = json.loads(gcs.get_bytes(obj).decode())
    except Exception:  # noqa: BLE001
        print(f"[fts] no corpus.json at {obj} — run the pubs ingest first; 0 articles")
        return []
    rows = [
        _row(id=f"article:{a.get('id')}", kind="article", title=a.get("title"),
             body=a.get("text") or "", sid=a.get("sid"), page=a.get("page"),
             volume=a.get("volume"), issue=a.get("issue"), topic=a.get("topic"),
             pdf=a.get("pdf"))
        for a in corpus if a.get("id")
    ]
    print(f"[fts] {len(rows)} articles from corpus.json")
    return rows


def _item_rows() -> list[tuple]:
    """One row per STAC item, from every collection's compact items.json under STAC_PREFIX."""
    index_paths = [p for p in gcs.list_paths(config.STAC_PREFIX) if p.endswith("/items.json")]

    def load(path: str) -> list[tuple]:
        try:
            doc = json.loads(gcs.get_bytes(path).decode())
        except Exception:  # noqa: BLE001
            return []
        coll = doc.get("collection") or ""
        out = []
        for it in doc.get("items") or []:
            props = it.get("properties") or {}
            kw = props.get("keywords")
            keywords = " ".join(kw) if isinstance(kw, list) else (kw or "")
            body = " ".join(str(props[k]) for k in _ITEM_BODY_PROPS if props.get(k))
            out.append(_row(
                id=f"item:{it.get('id')}", kind="item",
                title=props.get("title") or it.get("id"), body=body, keywords=keywords,
                series=props.get("ugs:series"), topic=props.get("ugs:topic"),
                coll_id=coll, item_id=it.get("id")))
        return out

    with ThreadPoolExecutor(max_workers=16) as ex:
        rows = [r for batch in ex.map(load, index_paths) for r in batch]
    print(f"[fts] {len(rows)} items from {len(index_paths)} collection indexes")
    return rows


def build() -> int:
    """Assemble all three grains into one FTS .duckdb and upload it. Returns total rows."""
    import duckdb

    rows = _pub_rows() + _article_rows() + _item_rows()
    if not rows:
        print("[fts] no rows from any grain — nothing to index; skipping")
        return 0

    work = tempfile.mkdtemp(prefix="fts_")
    db_path = os.path.join(work, "pubs-fts.duckdb")
    try:
        con = duckdb.connect(db_path)
        con.execute("INSTALL fts; LOAD fts;")
        con.execute(
            "CREATE TABLE docs(id VARCHAR, kind VARCHAR, title VARCHAR, body VARCHAR, "
            "keywords VARCHAR, series VARCHAR, year VARCHAR, pdf VARCHAR, sid VARCHAR, "
            "page INTEGER, volume VARCHAR, issue VARCHAR, topic VARCHAR, coll_id VARCHAR, "
            "item_id VARCHAR)")
        placeholders = ",".join(["?"] * len(COLS))
        for i in range(0, len(rows), 500):
            con.executemany(f"INSERT INTO docs VALUES ({placeholders})", rows[i:i + 500])
        n = len(rows)
        print(f"[fts] inserted {n} docs (pub/article/item); building FTS index…")
        # title + body + keywords are indexed; title weighted higher at query time.
        con.execute("PRAGMA create_fts_index('docs', 'id', 'title', 'body', 'keywords', overwrite=1)")
        con.close()
        gcs.upload(db_path, FTS_OBJECT, content_type="application/octet-stream",
                   cache_control=gcs.CACHE_MUTABLE)
        size_mb = os.path.getsize(db_path) // 1024 // 1024
        print(f"[fts] {n} docs → {config.public_url(FTS_OBJECT)} ({size_mb} MB)")
        return n
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main() -> int:
    return 0 if build() >= 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
