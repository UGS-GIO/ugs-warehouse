// Full-text search across ALL publications — client-side, no server. Queries the warehouse-built
// DuckDB FTS database (pubs/search/pubs-fts.duckdb) in the browser via duckdb-wasm, which
// **range-reads** the remote file (fetches only the index pages a query touches — spiked + confirmed).
// The engine is self-hosted (see ./duckdb); the DB is fetched lazily on first use, so it costs nothing
// until the "full text" toggle is switched on.
import { attach, type Conn } from "./duckdb";

export type PubHit = { id: string; title: string; series: string; pdf?: string; score: number };

export const FTS_DB_URL = new URL(
  new URLSearchParams(location.search).get("ftsdb")
    || "https://maps-assets.geology.utah.gov/pubs/search/pubs-fts.duckdb",
  location.href,
).href;

let _conn: Promise<Conn> | null = null;
function conn() { return (_conn ??= attach(FTS_DB_URL, "s", "fts")); }

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
