"""Build the publications knowledge graph: nodes + edges as Parquet on the CDN.

The analytics foundation on top of the FTS / embedding work. Every "lens" (citation network,
co-authorship clusters, semantic neighbours, ...) is just a query over these two tables — run
client-side by duckdb-wasm (the same engine the search uses), or from DuckDB / a notebook.

Nodes (graph/nodes.parquet):  node_id ('pub:OFR-730', 'author:douglas-a-sprinkel', 'series:OFR',
  'topic:groundwater'), kind (pub|author|series|topic), label, props (JSON string).
Edges (graph/edges.parquet):  src, dst, rel, weight, props(JSON).
  authored  author -> pub          coauthor  author <-> author (shared pub)
  in_series pub -> series          has_topic pub -> topic
  cites     pub -> pub  (UGS series-ids mined from full text, validated against real ids)
  similar   pub <-> pub (top-k cosine over mean-pooled chunk embeddings)

    python -m ugs_warehouse.pubs.graph
"""
from __future__ import annotations

import io
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor

from ..core import config, gcs
from . import identity, source

GRAPH_PREFIX = os.environ.get("PUB_GRAPH_PREFIX", "graph")
NODES_OBJECT = f"{GRAPH_PREFIX}/nodes.parquet"
EDGES_OBJECT = f"{GRAPH_PREFIX}/edges.parquet"
PARQUET_MIME = config.PARQUET_MIME
SIMILAR_K = int(os.environ.get("PUB_GRAPH_SIMILAR_K", "5"))   # nearest neighbours per pub

# A token that *looks* like a UGS publication id; the real test is membership in the id set below.
_CITE_TOKEN = re.compile(r"\b[A-Za-z]{1,4}-\d{1,5}(?:-\d{1,3})?[A-Za-z]{0,4}\b")
# Split a secondary-author field into individual names.
_AUTHOR_SPLIT = re.compile(r"\s+and\s+|\s*[;,&]\s*", re.IGNORECASE)


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.strip().lower()).strip("-")


def _authors(p: dict) -> list[str]:
    """Ordered, de-duplicated author names for a pub (primary first, then parsed secondaries)."""
    names: list[str] = []
    primary = (p.get("pub_author") or "").strip()
    if primary:
        names.append(primary)
    for n in _AUTHOR_SPLIT.split((p.get("pub_sec_author") or "").strip()):
        n = n.strip()
        if n:
            names.append(n)
    seen, out = set(), []
    for n in names:
        k = _slug(n)
        if k and k not in seen:
            seen.add(k)
            out.append(n)
    return out


def _topics(p: dict) -> list[str]:
    raw = (p.get("keywords") or "")
    return [t.strip() for t in re.split(r"[;,]", raw) if t.strip()]


def _mean_vec(npz_bytes: bytes):
    import numpy as np
    with np.load(io.BytesIO(npz_bytes), allow_pickle=True) as z:
        emb = z["emb"]
    if emb is None or len(emb) == 0:
        return None
    v = emb.mean(axis=0).astype("float32")
    n = float((v * v).sum()) ** 0.5
    return v / n if n else None


def _similar_edges(pub_ids: set[str]) -> list[dict]:
    """top-k cosine neighbours per pub, from mean-pooled chunk embeddings (graph 'similar' edges)."""
    import numpy as np

    pfx = identity.PUB_EMB_PREFIX.rstrip("/") + "/"
    paths = [p for p in gcs.list_paths(identity.PUB_EMB_PREFIX) if p.endswith(".npz")]
    if not paths:
        print("[graph] no embeddings — skipping 'similar' edges (run the embed job first)")
        return []

    def load(path: str):
        sid = path[len(pfx):][:-len(".npz")].upper() if path.startswith(pfx) else None
        if not sid or sid not in pub_ids:
            return None
        try:
            v = _mean_vec(gcs.get_bytes(path))
        except Exception:  # noqa: BLE001
            return None
        return (sid, v) if v is not None else None

    with ThreadPoolExecutor(max_workers=16) as ex:
        loaded = [r for r in ex.map(load, paths) if r is not None]
    if not loaded:
        return []
    ids = [sid for sid, _ in loaded]
    mat = np.vstack([v for _, v in loaded])         # (N, 384), already L2-normalised
    sims = mat @ mat.T                              # cosine (unit vectors)
    np.fill_diagonal(sims, -1.0)
    k = min(SIMILAR_K, len(ids) - 1)
    edges: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for i, sid in enumerate(ids):
        for j in np.argsort(sims[i])[::-1][:k]:
            a, b = sorted((sid, ids[j]))
            if (a, b) in seen:
                continue
            seen.add((a, b))
            edges.append({"src": f"pub:{a}", "dst": f"pub:{b}", "rel": "similar",
                          "weight": round(float(sims[i][j]), 4), "props": "{}"})
    print(f"[graph] similar: {len(edges)} edges over {len(ids)} embedded pubs")
    return edges


def _cite_edges(pub_ids: set[str]) -> list[dict]:
    """pub -> pub citation edges, mined from each pub's full text (tokens validated against real ids)."""
    pfx = identity.PUB_FULLTEXT_PREFIX.rstrip("/") + "/"
    paths = [p for p in gcs.list_paths(identity.PUB_FULLTEXT_PREFIX) if p.endswith(".txt")]

    def scan(path: str):
        sid = path[len(pfx):][:-len(".txt")].upper() if path.startswith(pfx) else None
        if not sid:
            return None
        try:
            text = gcs.get_bytes(path).decode("utf-8", "ignore")
        except Exception:  # noqa: BLE001
            return None
        counts: dict[str, int] = {}
        for tok in _CITE_TOKEN.findall(text):
            t = tok.upper()
            if t in pub_ids and t != sid:        # real pub id, not a self-reference
                counts[t] = counts.get(t, 0) + 1
        return (sid, counts)

    edges: list[dict] = []
    with ThreadPoolExecutor(max_workers=16) as ex:
        for res in ex.map(scan, paths):
            if not res:
                continue
            sid, counts = res
            for tgt, n in counts.items():
                edges.append({"src": f"pub:{sid}", "dst": f"pub:{tgt}", "rel": "cites",
                              "weight": n, "props": "{}"})
    print(f"[graph] cites: {len(edges)} edges from {len(paths)} full-text docs")
    return edges


def build() -> int:
    import pandas as pd

    pubs = source.read_pubs()
    pub_ids = {(p.get("series_id") or "").strip().upper() for p in pubs if (p.get("series_id") or "").strip()}

    nodes: dict[str, dict] = {}      # node_id -> node row (dedup)
    edges: list[dict] = []

    def add_node(node_id: str, kind: str, label: str, props: dict | None = None):
        nodes.setdefault(node_id, {"node_id": node_id, "kind": kind, "label": label,
                                   "props": json.dumps(props or {})})

    for p in pubs:
        sid = (p.get("series_id") or "").strip().upper()
        if not sid:
            continue
        pid = f"pub:{sid}"
        add_node(pid, "pub", (p.get("pub_name") or sid).strip(),
                 {"year": (p.get("pub_year") or "").strip(), "series": (p.get("series") or "").strip(),
                  "scale": (p.get("pub_scale") or "").strip()})
        series = (p.get("series") or "").strip()
        if series:
            sn = f"series:{_slug(series)}"
            add_node(sn, "series", series)
            edges.append({"src": pid, "dst": sn, "rel": "in_series", "weight": 1, "props": "{}"})
        for t in _topics(p):
            tn = f"topic:{_slug(t)}"
            add_node(tn, "topic", t)
            edges.append({"src": pid, "dst": tn, "rel": "has_topic", "weight": 1, "props": "{}"})
        auths = _authors(p)
        anodes = []
        for name in auths:
            an = f"author:{_slug(name)}"
            add_node(an, "author", name)
            anodes.append(an)
            edges.append({"src": an, "dst": pid, "rel": "authored", "weight": 1, "props": "{}"})
        for i in range(len(anodes)):           # co-authorship (undirected, one row per pair)
            for j in range(i + 1, len(anodes)):
                a, b = sorted((anodes[i], anodes[j]))
                edges.append({"src": a, "dst": b, "rel": "coauthor", "weight": 1, "props": "{}"})

    edges += _cite_edges(pub_ids)
    edges += _similar_edges(pub_ids)

    nodes_df = pd.DataFrame(list(nodes.values()))
    edges_df = pd.DataFrame(edges)
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        npath = os.path.join(tmp, "nodes.parquet")
        epath = os.path.join(tmp, "edges.parquet")
        nodes_df.to_parquet(npath, index=False)
        edges_df.to_parquet(epath, index=False)
        gcs.upload(npath, NODES_OBJECT, content_type=PARQUET_MIME, cache_control=gcs.CACHE_MUTABLE)
        gcs.upload(epath, EDGES_OBJECT, content_type=PARQUET_MIME, cache_control=gcs.CACHE_MUTABLE)
    by_rel = edges_df["rel"].value_counts().to_dict() if len(edges_df) else {}
    print(f"[graph] {len(nodes_df)} nodes, {len(edges_df)} edges {by_rel}")
    print(f"[graph] -> {config.public_url(NODES_OBJECT)} + {config.public_url(EDGES_OBJECT)}")
    return len(nodes_df)


def main() -> int:
    return 0 if build() >= 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
