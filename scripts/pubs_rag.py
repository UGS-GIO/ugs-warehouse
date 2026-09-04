"""Fully-local semantic RAG over UGS publications — free, offline after model pull.

Pairs the warehouse's embedding search (pubs-vss.duckdb) with a local LLM:

  1. Embed the query with the SAME model the warehouse used (fastembed BAAI/bge-small-en-v1.5,
     384-dim) so it lands in the same vector space as the stored chunk embeddings. Ollama's embed
     models are 768/1024-dim — wrong space — so we embed here, not via Ollama.
  2. Semantic-retrieve nearest chunks from pubs-vss.duckdb (HNSW cosine, range-read off the CDN).
  3. Generate a grounded, cited answer with a local Ollama model (default qwen2.5:32b).

No GCP perms (the .duckdb is public on the CDN). No API bills. Retrieval is CPU; only generation
uses the model. The viewer's semantic search was dropped in 69c7bfd (4 CVEs), so nothing
mirrors this embedding path client-side any more.

    ollama pull qwen2.5:32b          # once (generation)
    ollama serve                     # background
    python -m scripts.pubs_rag "what triggers debris flows after wildfires?"
    python -m scripts.pubs_rag "landslide reactivation from septic systems" --k 8 --model qwen2.5:14b
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request

VSS_URL = "https://maps-assets.geology.utah.gov/pubs/search/pubs-vss.duckdb"
EMBED_MODEL = "BAAI/bge-small-en-v1.5"          # == warehouse (pubs/embed.py) and viewer twin
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "   # bge retrieval instruction
OLLAMA = "http://localhost:11434"


def embed_query(q: str) -> list[float]:
    """384-dim query vector, same model + prefix the warehouse embedded passages with."""
    from fastembed import TextEmbedding

    model = TextEmbedding(EMBED_MODEL)          # first run downloads ~130MB, then cached
    return list(next(iter(model.embed([QUERY_PREFIX + q]))))


def retrieve(vec: list[float], k: int, db_url: str) -> list[dict]:
    """Nearest chunks by cosine distance, deduped to the best chunk per publication."""
    import duckdb

    con = duckdb.connect()
    con.execute("INSTALL vss; LOAD vss; INSTALL httpfs; LOAD httpfs;")
    con.execute("SET hnsw_enable_experimental_persistence=true;")
    con.execute(f"ATTACH '{db_url}' AS v (READ_ONLY); USE v;")
    vlit = "[" + ",".join(repr(float(x)) for x in vec) + "]::FLOAT[384]"
    rows = con.execute(
        f"SELECT pub_id, title, series, pdf, snippet, "
        f"       array_cosine_distance(emb, {vlit}) AS dist "
        f"FROM chunks ORDER BY dist LIMIT 200"
    ).fetchall()
    cols = ["pub_id", "title", "series", "pdf", "snippet", "dist"]
    seen: set[str] = set()
    out: list[dict] = []
    for r in rows:
        d = dict(zip(cols, r))
        if d["pub_id"] in seen:                 # best (nearest) chunk per pub — matches vsearch.ts
            continue
        seen.add(d["pub_id"])
        out.append(d)
        if len(out) >= k:
            break
    return out


def generate(q: str, hits: list[dict], model: str) -> str:
    """Grounded answer from the retrieved snippets, streamed from a local Ollama model."""
    ctx = "\n\n".join(
        f"[{h['pub_id']}] {h['title']} ({h['series']})\n{h['snippet']}" for h in hits
    )
    prompt = (
        "You are a UGS publications assistant. Answer the question USING ONLY the sources below. "
        "Cite the publication id in brackets, e.g. [RI-232], after each claim. If the sources do "
        "not cover it, say so.\n\n"
        f"SOURCES:\n{ctx}\n\nQUESTION: {q}\n\nANSWER:"
    )
    body = json.dumps({"model": model, "prompt": prompt, "stream": True}).encode()
    req = urllib.request.Request(f"{OLLAMA}/api/generate", data=body,
                                 headers={"Content-Type": "application/json"})
    parts: list[str] = []
    with urllib.request.urlopen(req) as resp:
        for line in resp:
            line = line.strip()
            if not line:
                continue
            tok = json.loads(line).get("response", "")
            parts.append(tok)
            sys.stdout.write(tok)
            sys.stdout.flush()
    sys.stdout.write("\n")
    return "".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser(description="Local semantic RAG over UGS publications.")
    ap.add_argument("query")
    ap.add_argument("--k", type=int, default=6, help="pubs to feed the model (default 6)")
    ap.add_argument("--model", default="qwen2.5:32b", help="Ollama model for generation")
    ap.add_argument("--db", default=VSS_URL, help="pubs-vss.duckdb URL")
    ap.add_argument("--sources-only", action="store_true", help="print retrieved pubs, skip the LLM")
    args = ap.parse_args()

    hits = retrieve(embed_query(args.query), args.k, args.db)
    print("── retrieved ──", file=sys.stderr)
    for h in hits:
        print(f"  [{h['pub_id']}] {h['title'][:70]}  (cos {h['dist']:.3f})", file=sys.stderr)
    print("───────────────\n", file=sys.stderr)
    if args.sources_only:
        return 0
    generate(args.query, hits, args.model)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
