"""Build the all-pub full-text-search database: a DuckDB file with a BM25 FTS index over every
publication's whole-document text.

Reads the per-pub text sidecars (`pubs/fulltext/{SID}.txt`, written by the thumbs job) + pub
metadata, loads them into a `docs` table, runs `PRAGMA create_fts_index`, and uploads the `.duckdb`
to the CDN. The viewer queries it **client-side via duckdb-wasm range reads** — no server, the
browser fetches only the index pages a query touches (spiked + confirmed). Run as its own Cloud Run
job after the thumbs full-text pass.

    python -m ugs_warehouse.pubs.fts
"""
from __future__ import annotations

import os
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor

from ..core import config, gcs
from . import identity, sink_stac, source

# Object the .duckdb lands at on the CDN; the viewer ATTACHes this URL.
FTS_OBJECT = os.environ.get("PUB_FTS_OBJECT", "pubs/search/pubs-fts.duckdb")


def build() -> int:
    """List the fulltext sidecars, join pub metadata, build the FTS .duckdb, upload it. Returns rows."""
    import duckdb

    meta = {(p.get("series_id") or "").strip().upper(): p for p in source.read_pubs()}
    paths = [p for p in gcs.list_paths(identity.PUB_FULLTEXT_PREFIX) if p.endswith(".txt")]
    print(f"[fts] {len(paths)} fulltext docs; {len(meta)} pubs in metadata")
    if not paths:
        print("[fts] no fulltext sidecars — run the thumbs full-text pass first; skipping")
        return 0

    def load(path: str) -> tuple | None:
        sid = path.rsplit("/", 1)[-1][:-len(".txt")].upper()
        try:
            body = gcs.get_bytes(path).decode("utf-8", "ignore")
        except Exception:  # noqa: BLE001
            return None
        if not body.strip():
            return None
        p = meta.get(sid, {})
        return (sid, (p.get("pub_name") or "").strip(), (p.get("series") or "").strip(),
                str(p.get("pub_year") or "").strip(), sink_stac.href(p.get("pub_url")), body)

    work = tempfile.mkdtemp(prefix="fts_")
    db_path = os.path.join(work, "pubs-fts.duckdb")
    try:
        con = duckdb.connect(db_path)
        con.execute("INSTALL fts; LOAD fts;")
        con.execute("CREATE TABLE docs(id VARCHAR, title VARCHAR, series VARCHAR, "
                    "year VARCHAR, pdf VARCHAR, body VARCHAR)")
        batch, n = [], 0
        with ThreadPoolExecutor(max_workers=16) as ex:
            for row in ex.map(load, paths):
                if row is None:
                    continue
                batch.append(row)
                if len(batch) >= 500:
                    con.executemany("INSERT INTO docs VALUES (?,?,?,?,?,?)", batch)
                    n += len(batch); batch = []
        if batch:
            con.executemany("INSERT INTO docs VALUES (?,?,?,?,?,?)", batch); n += len(batch)
        print(f"[fts] inserted {n} docs; building FTS index…")
        # `id` is the document key; title + body are indexed (title weighted higher at query time).
        con.execute("PRAGMA create_fts_index('docs', 'id', 'title', 'body', overwrite=1)")
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
