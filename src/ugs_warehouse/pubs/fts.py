"""Build the all-pub full-text-search database: a DuckDB file with a BM25 FTS index over every
publication's whole-document text.

Reads the per-pub text sidecars (`pubs/fulltext/{SID}.txt`, written by the thumbs job) + pub
metadata, loads them into a `docs` table, runs `PRAGMA create_fts_index`, and uploads the `.duckdb`
to the CDN. The viewer queries it **client-side via duckdb-wasm range reads** — no server, the
browser fetches only the index pages a query touches (spiked + confirmed). Run as its own Cloud Run
job after the thumbs full-text pass. It skips the rebuild when no sidecar and no pub's stored fields
changed since the last build.

    python -m ugs_warehouse.pubs.fts [--force]
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor

from ..core import config, gcs
from . import identity, sink_stac, source

# Object the .duckdb lands at on the CDN; the viewer ATTACHes this URL.
FTS_OBJECT = os.environ.get("PUB_FTS_OBJECT", "pubs/search/pubs-fts.duckdb")
# What the last build was made from. Bump BUILD_VERSION when build() changes what the database holds.
FINGERPRINT_OBJECT = f"{FTS_OBJECT}.fingerprint"
BUILD_VERSION = "1"


def _fields(p: dict) -> tuple[str, str, str, str]:
    """A pub's title, series, year and PDF URL, as the database stores them."""
    return ((p.get("pub_name") or "").strip(), (p.get("series") or "").strip(),
            str(p.get("pub_year") or "").strip(), sink_stac.href(p.get("pub_url")))


def _sid(path: str) -> str:
    return path.rsplit("/", 1)[-1].removesuffix(".txt").upper()


def fingerprint(etags: dict[str, str], meta: dict[str, dict]) -> str:
    """Changes when a sidecar is added, removed or rewritten, or a pub's stored fields change."""
    rows = [(path, etag, _fields(meta.get(_sid(path), {}))) for path, etag in sorted(etags.items())]
    return hashlib.sha256(json.dumps([BUILD_VERSION, rows]).encode()).hexdigest()


def build(force: bool = False) -> int:
    """List the fulltext sidecars, join pub metadata, build the FTS .duckdb, upload it. Returns rows,
    or 0 when nothing changed since the last build."""
    meta = {(p.get("series_id") or "").strip().upper(): p for p in source.read_pubs()}
    etags = {p: e for p, e in gcs.list_etags(identity.PUB_FULLTEXT_PREFIX).items()
             if p.endswith(".txt")}
    print(f"[fts] {len(etags)} fulltext docs; {len(meta)} pubs in metadata")
    if not etags:
        print("[fts] no fulltext sidecars — run the thumbs full-text pass first; skipping")
        return 0
    fp = fingerprint(etags, meta)
    if not force and gcs.exists(FTS_OBJECT) and _last_fingerprint() == fp:
        print("[fts] sidecars and metadata unchanged since the last build; skipping")
        return 0
    n = _build_db(sorted(etags), meta)
    gcs.put_bytes(fp.encode(), FINGERPRINT_OBJECT, content_type="text/plain",
                  cache_control=gcs.CACHE_MUTABLE)
    return n


def _last_fingerprint() -> str | None:
    try:
        return gcs.get_bytes(FINGERPRINT_OBJECT).decode()
    except FileNotFoundError:
        return None


def _build_db(paths: list[str], meta: dict[str, dict]) -> int:
    import duckdb

    def load(path: str) -> tuple | None:
        sid = _sid(path)
        try:
            body = gcs.get_bytes(path).decode("utf-8", "ignore")
        except Exception:  # noqa: BLE001
            return None
        if not body.strip():
            return None
        return (sid, *_fields(meta.get(sid, {})), body)

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
                    n += len(batch)
                    batch = []
        if batch:
            con.executemany("INSERT INTO docs VALUES (?,?,?,?,?,?)", batch)
            n += len(batch)
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
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--force", action="store_true", help="rebuild even when nothing changed")
    return 0 if build(force=ap.parse_args().force) >= 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
