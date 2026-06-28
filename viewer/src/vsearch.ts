// Semantic search across all publications — client-side, no server. Embeds the query in the browser
// (transformers.js, Xenova/bge-small-en-v1.5 — the ONNX twin of the model the warehouse used) and
// runs nearest-neighbour search against the DuckDB VSS database (pubs/search/pubs-vss.duckdb) via
// duckdb-wasm, which **range-reads** the remote HNSW index (spiked + confirmed). Both engines load
// lazily from CDN on first use, so this costs nothing until the "semantic" toggle is switched on.
//
// PROD NOTE: the model + duckdb-wasm/vss bundles load from HF / jsDelivr / duckdb.org here. For
// production, self-host them on the maps-assets CDN and set transformers.js `env.remoteHost`.

export type VHit = { pubId: string; title: string; series: string; pdf?: string; snippet: string; dist: number };

export const VSS_DB_URL = new URL(
  new URLSearchParams(location.search).get("vssdb")
    || "https://maps-assets.geology.utah.gov/pubs/search/pubs-vss.duckdb",
  location.href,
).href;

const XFORMERS_ESM = "https://cdn.jsdelivr.net/npm/@xenova/transformers@2.17.2";
const DUCKDB_ESM = "https://cdn.jsdelivr.net/npm/@duckdb/duckdb-wasm@1.29.0/+esm";
// bge retrieval: passages are embedded as-is (the warehouse did), the QUERY gets this instruction.
const QUERY_PREFIX = "Represent this sentence for searching relevant passages: ";

let _embed: Promise<(t: string, o: object) => Promise<{ data: Float32Array }>> | null = null;
let _conn: Promise<{ query: (sql: string) => Promise<{ toArray: () => Record<string, unknown>[] }> }> | null = null;

function embedder() {
  return (_embed ??= (async () => {
    const t = await import(/* @vite-ignore */ XFORMERS_ESM);
    t.env.allowLocalModels = false;   // skip the local-model probe → straight to the model host
    return await t.pipeline("feature-extraction", "Xenova/bge-small-en-v1.5");
  })());
}

function conn() {
  return (_conn ??= (async () => {
    const duckdb = await import(/* @vite-ignore */ DUCKDB_ESM);
    const bundle = await duckdb.selectBundle(duckdb.getJsDelivrBundles());
    const worker = await duckdb.createWorker(bundle.mainWorker);
    const db = new duckdb.AsyncDuckDB(new duckdb.ConsoleLogger(), worker);
    await db.instantiate(bundle.mainModule, bundle.pthreadWorker);
    const c = await db.connect();
    await db.registerFileURL("pubs-vss.duckdb", VSS_DB_URL, duckdb.DuckDBDataProtocol.HTTP, false);
    await c.query("INSTALL vss; LOAD vss;");
    await c.query("ATTACH 'pubs-vss.duckdb' AS s (READ_ONLY)");
    await c.query("USE s");
    return c;
  })());
}

/** Semantic (vector) search: nearest publication chunks to the query, deduped to one row per pub. */
export async function semanticSearch(q: string): Promise<VHit[]> {
  const embed = await embedder();
  const o = await embed(QUERY_PREFIX + q, { pooling: "mean", normalize: true });
  const vlit = "[" + Array.from(o.data).join(",") + "]::FLOAT[384]";
  const c = await conn();
  const res = await c.query(
    `SELECT pub_id, title, series, pdf, snippet, array_cosine_distance(emb, ${vlit}) AS dist
     FROM chunks ORDER BY dist LIMIT 40`);
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
