// Semantic search across all publications — client-side, no server. Embeds the query in the browser
// (transformers.js, Xenova/bge-small-en-v1.5 — the ONNX twin of the model the warehouse used) and
// runs nearest-neighbour search against the DuckDB VSS database (pubs/search/pubs-vss.duckdb) via
// duckdb-wasm, which **range-reads** the remote HNSW index (spiked + confirmed). Both engines load
// lazily on first use, so this costs nothing until the "semantic" toggle is switched on.
//
// Self-hosted: the DuckDB engine ships from our own bundle (see ./duckdb). The bge model is served
// from the maps-assets CDN (override the host with `?models=`), so the read path has no HF dependency.
import { attach, type Conn } from "./duckdb";

export type VHit = { pubId: string; title: string; series: string; pdf?: string; snippet: string; dist: number };

export const VSS_DB_URL = new URL(
  new URLSearchParams(location.search).get("vssdb")
    || "https://maps-assets.geology.utah.gov/pubs/search/pubs-vss.duckdb",
  location.href,
).href;

// Where the ONNX model files live. Default = our CDN (self-hosted); `?models=` overrides for spikes.
// Trailing slash matters — transformers.js joins host + "{model}/" + file into the fetch URL.
const MODEL_HOST = (new URLSearchParams(location.search).get("models")
  || "https://maps-assets.geology.utah.gov/pubs/models").replace(/\/?$/, "/");
// bge retrieval: passages are embedded as-is (the warehouse did), the QUERY gets this instruction.
const QUERY_PREFIX = "Represent this sentence for searching relevant passages: ";

let _embed: Promise<(t: string, o: object) => Promise<{ data: Float32Array }>> | null = null;
let _conn: Promise<Conn> | null = null;

function embedder() {
  return (_embed ??= (async () => {
    const { pipeline, env } = await import("@xenova/transformers");   // lazy — onnxruntime stays out of main
    env.allowLocalModels = false;          // skip the local-model probe → straight to the model host
    env.remoteHost = MODEL_HOST;           // self-hosted: bge-small ONNX from our CDN, not HF
    env.remotePathTemplate = "{model}/";   // {host}{model}/file → maps-assets/pubs/models/Xenova/bge.../…
    return await pipeline("feature-extraction", "Xenova/bge-small-en-v1.5") as unknown as
      (t: string, o: object) => Promise<{ data: Float32Array }>;
  })());
}

function conn() { return (_conn ??= attach(VSS_DB_URL, "s", "vss")); }

/** Semantic (vector) search: nearest publication chunks to the query, deduped to one row per pub. */
export async function semanticSearch(q: string): Promise<VHit[]> {
  const embed = await embedder();
  const o = await embed(QUERY_PREFIX + q, { pooling: "mean", normalize: true });
  const vlit = "[" + Array.from(o.data).join(",") + "]::FLOAT[384]";
  const c = await conn();
  // Over-fetch chunks, then keep the best chunk per pub. A pub is only as close as its nearest
  // chunk, but many pubs have several near chunks — a tight 40 fills up with a few pubs' duplicates
  // and drops pubs whose best chunk ranks just past the cut. 200 candidates → a fuller top-20 pubs.
  const res = await c.query(
    `SELECT pub_id, title, series, pdf, snippet, array_cosine_distance(emb, ${vlit}) AS dist
     FROM chunks ORDER BY dist LIMIT 200`);
  const seen = new Set<string>();
  const out: VHit[] = [];
  for (const r of res.toArray()) {
    const pubId = String(r.pub_id);
    if (seen.has(pubId)) continue;   // best (nearest) chunk per pub
    seen.add(pubId);
    out.push({
      pubId, title: String(r.title ?? pubId), series: String(r.series ?? ""),
      pdf: r.pdf ? String(r.pdf) : undefined, snippet: String(r.snippet ?? ""), dist: Number(r.dist),
    });
  }
  return out.slice(0, 20);
}
