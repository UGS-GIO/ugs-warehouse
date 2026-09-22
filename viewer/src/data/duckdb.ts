// Shared duckdb-wasm engine for the client-side pub search (full-text + semantic). Self-hosted: the
// wasm + worker ship from our own bundle (vite `?url` → hashed assets in dist/), NOT jsDelivr, so the
// read path has no third-party runtime dependency. `selectBundle` picks the `eh` build on modern
// browsers (only that one wasm is fetched), `mvp` as the fallback — both lazy, nothing loads until the
// first search. Remote files (the `.duckdb` ATTACHed here, and the GeoParquet the data explorer reads)
// are **range-read** (206 partials) because newDb() opens with forceFullHTTPReads off, so duckdb
// fetches only the pages/chunks a query touches, never the whole file.
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
  return newDb(await import("@duckdb/duckdb-wasm"));
}

async function newDb(duckdb: typeof import("@duckdb/duckdb-wasm")) {
  const bundle = await duckdb.selectBundle(BUNDLES);
  const worker = new Worker(bundle.mainWorker!);
  const db = new duckdb.AsyncDuckDB(new duckdb.ConsoleLogger(), worker);
  await db.instantiate(bundle.mainModule, bundle.pthreadWorker);
  // duckdb-wasm >= 1.30 defaults forceFullHTTPReads=true, so every HTTP-registered file (parquet
  // here, the .duckdb ATTACH for pub search) is downloaded WHOLE into the WASM heap on first open
  // (1.3-2 GB for the wetlands layers), which OOMs the tab. Opening with it off makes DuckDB HTTP
  // range-read (206 partials): footer + only the projected column chunks per query. allowFullHTTPReads
  // off means a server without range support fails the query loudly instead of silently downloading
  // the whole file. Must run before any connect()/query. (ALL-6001)
  await db.open({ filesystem: { forceFullHTTPReads: false, allowFullHTTPReads: false } });
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
