// Shared duckdb-wasm engine for the client-side pub search (full-text + semantic). Self-hosted: the
// wasm + worker ship from our own bundle (vite `?url` → hashed assets in dist/), NOT jsDelivr, so the
// read path has no third-party runtime dependency. `selectBundle` picks the `eh` build on modern
// browsers (only that one wasm is fetched), `mvp` as the fallback — both lazy, nothing loads until the
// first search. The GeoParquet the explorer and the exporter read is **range-read** (206 partials,
// see newDb) so a query fetches only the chunks it projects; the `.duckdb` ATTACHed here is read
// whole, which is fewer bytes for an index a search walks all over.
// Asset URLs are static `?url` imports (vite emits hashed asset paths — tiny strings, no engine code),
// so the heavy duckdb-wasm JS + onnxruntime stay out of the main bundle and load lazily (below).
import mvpWasm from "@duckdb/duckdb-wasm/dist/duckdb-mvp.wasm?url";
import mvpWorker from "@duckdb/duckdb-wasm/dist/duckdb-browser-mvp.worker.js?url";
import ehWasm from "@duckdb/duckdb-wasm/dist/duckdb-eh.wasm?url";
import ehWorker from "@duckdb/duckdb-wasm/dist/duckdb-browser-eh.worker.js?url";

export type Conn = { query: (sql: string) => Promise<{ toArray: () => Record<string, unknown>[] }> };

const BUNDLES = {
  mvp: { mainModule: mvpWasm, mainWorker: mvpWorker },
  eh: { mainModule: ehWasm, mainWorker: ehWorker },
};

// `?extrepo=` points extension autoload at our CDN mirror. Lazy: module scope `location` breaks
// every non-DOM import.
const extRepo = () =>
  (typeof location === "undefined" ? "" : new URLSearchParams(location.search).get("extrepo")) || "";

/** Boot an engine on the self-hosted bundle — one cache entry across search, diff and export. */
export async function newDuckDb(): Promise<import("@duckdb/duckdb-wasm").AsyncDuckDB> {
  return newDb(await import("@duckdb/duckdb-wasm"), true);
}

/** `rangeReads` suits a reader that touches part of a file: the parquet footer, a page of rows,
 *  the column chunks an export projects. duckdb-wasm >= 1.30 defaults forceFullHTTPReads=true, so
 *  without it every registered file is pulled whole into the wasm heap — that is what made a
 *  footer query on a 1.35 GB topic cost 63s (1.0s with ranges) and a 2 GB one crash the tab.
 *  Leave it off for a reader that needs the whole file anyway (the FTS index, the review diff):
 *  measured on the 485 MB pub-search db, ranges fetch ~19% MORE bytes in the same wall time. */
async function newDb(duckdb: typeof import("@duckdb/duckdb-wasm"), rangeReads = false) {
  const bundle = await duckdb.selectBundle(BUNDLES);
  const worker = new Worker(bundle.mainWorker!);
  const db = new duckdb.AsyncDuckDB(new duckdb.ConsoleLogger(), worker);
  await db.instantiate(bundle.mainModule, bundle.pthreadWorker);
  // Must run before any connect()/query. allowFullHTTPReads off so an origin without range
  // support fails loudly instead of silently downloading everything.
  if (rangeReads) await db.open({ filesystem: { forceFullHTTPReads: false, allowFullHTTPReads: false } });
  return db;
}

/** ATTACH a remote DuckDB over HTTP (range-read), load its extension, and switch into it so the
 * extension's macros (fts_main_*) resolve. Returns a ready connection. The engine JS loads lazily
 * here (dynamic import) — nothing downloads until the first search. */
export async function attach(dbUrl: string, alias: string, ext: "fts"): Promise<Conn> {
  const duckdb = await import("@duckdb/duckdb-wasm");
  const db = await newDb(duckdb);
  const conn = await db.connect();
  await db.registerFileURL(`${alias}.duckdb`, dbUrl, duckdb.DuckDBDataProtocol.HTTP, false);
  const repo = extRepo();
  if (repo) await conn.query(`SET custom_extension_repository='${repo.replace(/'/g, "''")}'`);
  await conn.query(`INSTALL ${ext}; LOAD ${ext};`);
  await conn.query(`ATTACH '${alias}.duckdb' AS ${alias} (READ_ONLY)`);
  await conn.query(`USE ${alias}`);   // so the extension's macros resolve against the attached db
  return conn;
}

/** Open a connection with remote parquet files registered for HTTP range-reads. Query them by their
 * registered name, e.g. `read_parquet('review.parquet')`. Used by the _review↔_current diff — no
 * extension needed (GeoParquet `geom` reads as raw WKB BLOB, so `md5(geom)` hashes geometry directly).
 *
 * Returns a `close()` that MUST be called when done: this spins up a dedicated engine + worker, so
 * without teardown every call leaks the worker thread + its WASM heap (tens of MB each). */
export async function openParquet(
  files: Record<string, string>,
): Promise<{ conn: Conn; close: () => Promise<void> }> {
  const duckdb = await import("@duckdb/duckdb-wasm");
  const db = await newDb(duckdb);
  const conn = await db.connect();
  for (const [name, url] of Object.entries(files)) {
    await db.registerFileURL(name, url, duckdb.DuckDBDataProtocol.HTTP, false);
  }
  // terminate() tears down the worker thread + frees the WASM heap; close the connection first.
  const close = async () => {
    try { await conn.close(); }
    finally { await db.terminate(); }
  };
  return { conn, close };
}
