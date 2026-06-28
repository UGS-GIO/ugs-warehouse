// Shared duckdb-wasm engine for the client-side pub search (full-text + semantic). Self-hosted: the
// wasm + worker ship from our own bundle (vite `?url` → hashed assets in dist/), NOT jsDelivr, so the
// read path has no third-party runtime dependency. `selectBundle` picks the `eh` build on modern
// browsers (only that one wasm is fetched), `mvp` as the fallback — both lazy, nothing loads until the
// first search. The remote `.duckdb` is ATTACHed over HTTP and **range-read** (206 partials — duckdb
// fetches only the index pages a query touches, never the whole file).
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

// DuckDB extensions (fts/vss) autoload from the duckdb-wasm extension repository. `?extrepo=` (or a
// future vendored default) points it at our CDN mirror so even the extension wasm is self-hosted.
const EXT_REPO = new URLSearchParams(location.search).get("extrepo") || "";

async function newDb(duckdb: typeof import("@duckdb/duckdb-wasm")) {
  const bundle = await duckdb.selectBundle(BUNDLES);
  const worker = new Worker(bundle.mainWorker!);
  const db = new duckdb.AsyncDuckDB(new duckdb.ConsoleLogger(), worker);
  await db.instantiate(bundle.mainModule, bundle.pthreadWorker);
  return db;
}

/** ATTACH a remote DuckDB over HTTP (range-read), load its extension, and switch into it so the
 * extension's macros (fts_main_*, vss) resolve. Returns a ready connection. The engine JS loads
 * lazily here (dynamic import) — nothing downloads until the first search. */
export async function attach(dbUrl: string, alias: string, ext: "fts" | "vss"): Promise<Conn> {
  const duckdb = await import("@duckdb/duckdb-wasm");
  const db = await newDb(duckdb);
  const conn = await db.connect();
  await db.registerFileURL(`${alias}.duckdb`, dbUrl, duckdb.DuckDBDataProtocol.HTTP, false);
  if (EXT_REPO) await conn.query(`SET custom_extension_repository='${EXT_REPO.replace(/'/g, "''")}'`);
  await conn.query(`INSTALL ${ext}; LOAD ${ext};`);
  await conn.query(`ATTACH '${alias}.duckdb' AS ${alias} (READ_ONLY)`);
  await conn.query(`USE ${alias}`);   // so the extension's macros resolve against the attached db
  return conn;
}
