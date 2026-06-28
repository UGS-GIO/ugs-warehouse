// Full-text search across ALL publications — client-side, no server. Queries the warehouse-built
// DuckDB FTS database (pubs/search/pubs-fts.duckdb) in the browser via duckdb-wasm, which
// **range-reads** the remote file (fetches only the index pages a query touches — spiked + confirmed).
// duckdb-wasm is loaded lazily from the jsDelivr CDN on first use, so it costs nothing until the
// "full text" toggle is switched on.

export type PubHit = { id: string; title: string; series: string; pdf?: string; score: number };

export const FTS_DB_URL = new URL(
  new URLSearchParams(location.search).get("ftsdb")
    || "https://maps-assets.geology.utah.gov/pubs/search/pubs-fts.duckdb",
  location.href,
).href;

// duckdb-wasm ESM from the CDN — dynamic, runtime, not bundled by vite.
const DUCKDB_ESM = "https://cdn.jsdelivr.net/npm/@duckdb/duckdb-wasm@1.29.0/+esm";

let _conn: Promise<{ query: (sql: string) => Promise<{ toArray: () => Record<string, unknown>[] }> }> | null = null;

async function connect() {
  const duckdb = await import(/* @vite-ignore */ DUCKDB_ESM);
  const bundle = await duckdb.selectBundle(duckdb.getJsDelivrBundles());
  const worker = await duckdb.createWorker(bundle.mainWorker);
  const db = new duckdb.AsyncDuckDB(new duckdb.ConsoleLogger(), worker);
  await db.instantiate(bundle.mainModule, bundle.pthreadWorker);
  const conn = await db.connect();
  await db.registerFileURL("pubs-fts.duckdb", FTS_DB_URL, duckdb.DuckDBDataProtocol.HTTP, false);
  await conn.query("INSTALL fts; LOAD fts;");
  await conn.query("ATTACH 'pubs-fts.duckdb' AS s (READ_ONLY)");
  await conn.query("USE s");   // so the FTS macro's tables resolve in the attached db
  return conn;
}

function conn() { return (_conn ??= connect()); }

/** BM25 full-text search over every publication's whole-document text. */
export async function searchPubs(q: string): Promise<PubHit[]> {
  const safe = q.replace(/'/g, "''");
  const c = await conn();
  const res = await c.query(
    `SELECT d.id, d.title, d.series, d.pdf, fts_main_docs.match_bm25(d.id, '${safe}') AS score
     FROM docs d WHERE score IS NOT NULL ORDER BY score DESC LIMIT 50`);
  return res.toArray().map((r: Record<string, unknown>) => ({
    id: String(r.id), title: String(r.title ?? r.id), series: String(r.series ?? ""),
    pdf: r.pdf ? String(r.pdf) : undefined, score: Number(r.score),
  }));
}
