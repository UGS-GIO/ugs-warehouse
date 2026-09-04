// Full-text search across ALL publications — client-side, no server. Queries the warehouse-built
// DuckDB FTS database (pubs/search/pubs-fts.duckdb) in the browser via duckdb-wasm, which
// **range-reads** the remote file (fetches only the index pages a query touches — spiked + confirmed).
// The engine is self-hosted (see ./duckdb); the DB is fetched lazily on first use, so it costs nothing
// until the "full text" toggle is switched on.
import { bm25Terms, bm25Where, parseQuery } from "@/data/query";
import { attach, type Conn } from "@/data/duckdb";

export type PubHit = { id: string; title: string; series: string; pdf?: string; score: number };

export const FTS_DB_URL = new URL(
  new URLSearchParams(location.search).get("ftsdb")
    || "https://maps-assets.geology.utah.gov/pubs/search/pubs-fts.duckdb",
  location.href,
).href;

let _conn: Promise<Conn> | null = null;
function conn() { return (_conn ??= attach(FTS_DB_URL, "s", "fts")); }

// The docs table's VARCHAR text columns (title + the full-text body), discovered once. Used for
// phrase / -exclude LIKE scans — the schema isn't hard-coded so a rename here won't silently break.
let _textCols: Promise<string[]> | null = null;
function textCols(c: Conn) {
  return (_textCols ??= (async () => {
    try {
      const rows = (await c.query("DESCRIBE docs")).toArray() as Record<string, unknown>[];
      const cols = rows
        .filter((r) => String(r.column_type ?? "").toUpperCase().includes("VARCHAR"))
        .map((r) => String(r.column_name))
        .filter((n) => !["id", "pdf", "series"].includes(n.toLowerCase()));
      return cols.length ? cols : ["title"];
    } catch { return ["title"]; }
  })());
}

/** BM25 full-text search over every publication's whole-document text, with the shared query grammar:
 *  bare/phrase words rank via match_bm25; phrases, -exclusions, and series/id/title fields become SQL
 *  predicates. A field/exclude-only query (no words to score) falls back to a flat filtered scan. */
export async function searchPubs(q: string): Promise<PubHit[]> {
  const query = parseQuery(q);
  const terms = bm25Terms(query);
  const c = await conn();
  const extra = bm25Where(query, await textCols(c));
  if (!terms && !extra.length) return [];

  const where: string[] = [];
  let scoreExpr = "1.0";
  if (terms) {
    scoreExpr = `fts_main_docs.match_bm25(d.id, '${terms.replace(/'/g, "''")}')`;
    where.push(`${scoreExpr} IS NOT NULL`);
  }
  where.push(...extra);
  const sql = `SELECT d.id, d.title, d.series, d.pdf, ${scoreExpr} AS score FROM docs d`
    + (where.length ? ` WHERE ${where.join(" AND ")}` : "")
    + ` ORDER BY score DESC LIMIT 50`;
  const res = await c.query(sql);
  return res.toArray().map((r: Record<string, unknown>) => ({
    id: String(r.id), title: String(r.title ?? r.id), series: String(r.series ?? ""),
    pdf: r.pdf ? String(r.pdf) : undefined, score: Number(r.score),
  }));
}
