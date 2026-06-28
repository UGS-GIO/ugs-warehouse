"""Build the semantic-search database: chunk every publication's text, embed each chunk (bge-small
via fastembed), and store the vectors in a DuckDB VSS (HNSW, cosine) database uploaded to the CDN.

The viewer embeds the *query* in-browser with the **same** model (transformers.js Xenova/bge-small-
en-v1.5) and runs the nearest-neighbour search via duckdb-wasm **range reads** — no server (spiked +
confirmed). Run after the thumbs full-text pass. Incremental-friendly: pub text is immutable, so a
chunk's embedding is computed once per pub and never changes; only the index re-builds.

    python -m ugs_warehouse.pubs.embed
"""
from __future__ import annotations

import io
import os
import shutil
import tempfile

from ..core import config, gcs
from . import identity, sink_stac, source

VSS_OBJECT = os.environ.get("PUB_VSS_OBJECT", "pubs/search/pubs-vss.duckdb")
MODEL = os.environ.get("EMBED_MODEL", "BAAI/bge-small-en-v1.5")  # 384-dim; viewer uses the Xenova/ ONNX twin
# Where fastembed keeps the model. The harvest image warms this dir at build time so the job never
# downloads from HF at runtime (an unauthenticated HF pull rate-limited + timed out the embed job once).
MODEL_CACHE = os.environ.get("FASTEMBED_CACHE") or None
CHUNK_CHARS = int(os.environ.get("EMBED_CHUNK_CHARS", "1200"))   # ~250 words/chunk — passage granularity
DIM = 384


def _emb_object(sid: str) -> str:
    return f"{identity.PUB_EMB_PREFIX}/{sid.upper()}.npz"


def _pub_vectors(sid: str, text: str, get_model):
    """(snippets, embeddings) for a pub — from the per-pub cache if present, else embed + cache it.

    Pubs are immutable, so this is computed once per pub and reused on every later rebuild; only NEW
    pubs hit the model. `get_model` is a thunk so the model only loads when there's a cache miss."""
    import numpy as np

    obj = _emb_object(sid)
    if gcs.exists(obj):
        try:
            with np.load(io.BytesIO(gcs.get_bytes(obj)), allow_pickle=True) as z:
                return list(z["snippets"]), z["emb"]
        except Exception:  # noqa: BLE001 — a corrupt cache entry just gets recomputed below
            pass
    chs = chunk(text)
    if not chs:
        return [], None
    embs = np.asarray(list(get_model().embed(chs)), dtype=np.float32)
    snippets = [c[:240] for c in chs]
    buf = io.BytesIO()
    np.savez_compressed(buf, emb=embs, snippets=np.array(snippets, dtype=object))
    gcs.put_bytes(buf.getvalue(), obj, content_type="application/octet-stream",
                  cache_control=gcs.CACHE_MUTABLE)
    return snippets, embs


def chunk(text: str, n: int = CHUNK_CHARS) -> list[str]:
    """Split text into ~n-char windows at word boundaries (drops tiny tail fragments)."""
    text = " ".join(text.split())
    out: list[str] = []
    i = 0
    while i < len(text):
        end = min(i + n, len(text))
        if end < len(text):
            sp = text.rfind(" ", i, end)
            if sp > i:
                end = sp
        out.append(text[i:end].strip())
        i = end
    return [c for c in out if len(c) > 30]


def build() -> int:
    import duckdb
    from fastembed import TextEmbedding

    meta = {(p.get("series_id") or "").strip().upper(): p for p in source.read_pubs()}
    paths = [p for p in gcs.list_paths(identity.PUB_FULLTEXT_PREFIX) if p.endswith(".txt")]
    if not paths:
        print("[embed] no fulltext sidecars — run the thumbs full-text pass first; skipping")
        return 0
    # Model loads lazily — only if a pub is missing from the embedding cache. A pure rebuild (every
    # pub already embedded) never touches the model at all.
    _model = []
    def get_model():
        if not _model:
            print(f"[embed] cache miss — loading model {MODEL}…")
            _model.append(TextEmbedding(MODEL, cache_dir=MODEL_CACHE))  # pre-warmed in the image; no HF pull
        return _model[0]

    work = tempfile.mkdtemp(prefix="vss_")
    db_path = os.path.join(work, "pubs-vss.duckdb")
    try:
        con = duckdb.connect(db_path)
        con.execute("INSTALL vss; LOAD vss; SET hnsw_enable_experimental_persistence=true;")
        con.execute(f"CREATE TABLE chunks(pub_id VARCHAR, title VARCHAR, series VARCHAR, "
                    f"pdf VARCHAR, snippet VARCHAR, emb FLOAT[{DIM}])")
        total, embedded = 0, 0
        for path in paths:
            sid = path.rsplit("/", 1)[-1][:-len(".txt")].upper()
            try:
                text = gcs.get_bytes(path).decode("utf-8", "ignore")
            except Exception:  # noqa: BLE001
                continue
            cached = gcs.exists(_emb_object(sid))
            snippets, embs = _pub_vectors(sid, text, get_model)
            if embs is None or len(snippets) == 0:
                continue
            if not cached:
                embedded += 1
            p = meta.get(sid, {})
            title = (p.get("pub_name") or "").strip()
            series = (p.get("series") or "").strip()
            pdf = sink_stac.href(p.get("pub_url"))
            rows = [(sid, title, series, pdf, snip, emb.tolist())
                    for snip, emb in zip(snippets, embs)]
            con.executemany("INSERT INTO chunks VALUES (?,?,?,?,?,?)", rows)
            total += len(rows)
        print(f"[embed] {embedded} pubs newly embedded; {len(paths) - embedded} reused from cache")
        print(f"[embed] {total} chunks; building HNSW index…")
        con.execute("CREATE INDEX hidx ON chunks USING HNSW (emb) WITH (metric='cosine')")
        con.close()
        gcs.upload(db_path, VSS_OBJECT, content_type="application/octet-stream",
                   cache_control=gcs.CACHE_MUTABLE)
        size_mb = os.path.getsize(db_path) // 1024 // 1024
        print(f"[embed] {total} chunks → {config.public_url(VSS_OBJECT)} ({size_mb} MB)")
        return total
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main() -> int:
    return 0 if build() >= 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
