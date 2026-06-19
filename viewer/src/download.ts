// Client-side export: read an item's GeoParquet over the CDN in DuckDB-WASM, then hand
// it to gdal3.js (real GDAL/OGR in WASM) for format conversion — no server, no perms.
// DuckDB + gdal3.js load lazily on first export.
//
// Split of responsibilities:
//   - DuckDB reads the remote GeoParquet (its native CSV writer + ST_AsGeoJSON work;
//     its GDAL output drivers are broken, so we don't use those).
//   - GeoJSON is built in JS from ST_AsGeoJSON; CSV via DuckDB's native COPY.
//   - GPKG / Shapefile / FileGDB / FlatGeobuf go through gdal3.js, whose OGR drivers
//     write these correctly (incl. Esri .gdb — OpenFileGDB write, GDAL ≥ 3.6). gdal3.js
//     is ~40 MB (wasm+data), so it's dynamically imported only when one is requested.

export type ExportFormat = "shp" | "gpkg" | "gdb" | "fgb" | "geojson" | "csv";

export const FORMATS: { id: ExportFormat; label: string }[] = [
  { id: "shp", label: "Shapefile (zip)" },
  { id: "gpkg", label: "GeoPackage" },
  { id: "gdb", label: "File Geodatabase (zip)" },
  { id: "fgb", label: "FlatGeobuf" },
  { id: "geojson", label: "GeoJSON" },
  { id: "csv", label: "CSV (WKT)" },
];

// The transform writes the geometry column as `geom` (GEOMETRY 4326).
const GEOM = "geom";

type DB = import("@duckdb/duckdb-wasm").AsyncDuckDB;
let dbPromise: Promise<DB> | null = null;

/** Lazily boot one shared DuckDB-WASM instance (jsDelivr bundle + worker). */
async function getDB(): Promise<DB> {
  if (dbPromise) return dbPromise;
  dbPromise = (async () => {
    const duckdb = await import("@duckdb/duckdb-wasm");
    const bundle = await duckdb.selectBundle(duckdb.getJsDelivrBundles());
    const workerUrl = URL.createObjectURL(
      new Blob([`importScripts("${bundle.mainWorker}");`], { type: "text/javascript" }),
    );
    const worker = new Worker(workerUrl);
    const db = new duckdb.AsyncDuckDB(new duckdb.ConsoleLogger(duckdb.LogLevel.WARNING), worker);
    await db.instantiate(bundle.mainModule, bundle.pthreadWorker);
    URL.revokeObjectURL(workerUrl);
    return db;
  })();
  return dbPromise;
}

function triggerDownload(bytes: Uint8Array, filename: string, mime: string): void {
  // Copy into a fresh ArrayBuffer-backed array (DuckDB buffers may be SharedArrayBuffer-backed).
  const copy = new Uint8Array(bytes.byteLength);
  copy.set(bytes);
  const url = URL.createObjectURL(new Blob([copy], { type: mime }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

// Arrow rows carry BigInt (int64 cols) + Dates; make them JSON-safe.
const sanitize = (v: unknown): unknown =>
  typeof v === "bigint" ? Number(v) : v instanceof Date ? v.toISOString() : v;

let seq = 0;

// Identifier / literal quoting for SQL built from column names + a user search term.
const ident = (c: string) => `"${c.replace(/"/g, '""')}"`;
const lit = (s: string) => `'${s.replace(/'/g, "''")}'`;

// Register each remote GeoParquet with DuckDB-WASM ONCE, reuse across paged queries — DuckDB
// pulls only the footer + needed row-groups per query over HTTP range reads, so paging a 7000-row
// table never downloads the whole file. Cache keyed by URL so the explorer's page/sort/search
// re-queries hit the same registered handle.
const registered = new Map<string, string>();
async function registerUrl(parquetUrl: string): Promise<string> {
  const hit = registered.get(parquetUrl);
  if (hit) return hit;
  const duckdb = await import("@duckdb/duckdb-wasm");
  const db = await getDB();
  const src = `q${++seq}.parquet`;
  await db.registerFileURL(src, parquetUrl, duckdb.DuckDBDataProtocol.HTTP, false);
  registered.set(parquetUrl, src);
  return src;
}

export type ColType = "number" | "text";
// Per-column filter: numeric columns get a range (min/max), everything else a substring match.
export type ColFilter =
  | { col: string; kind: "number"; min?: number; max?: number }
  | { col: string; kind: "text"; contains: string };

export interface PageOpts {
  limit: number;
  offset: number;
  orderBy?: string;       // column to sort by (ignored if not a real column)
  desc?: boolean;
  search?: string;        // free-text, matched case-insensitively across every column
  filters?: ColFilter[];  // per-column constraints, ANDed together (and with `search`)
}
export interface Page {
  columns: string[];
  types: Record<string, ColType>;   // displayed column → filter UI kind
  rows: Record<string, unknown>[];
  total: number;      // total matching rows (for paging), NOT just this page
  // Per-row [xmin,ymin,xmax,ymax] in 4326 (aligned with `rows`), read from the GeoParquet's
  // plain bbox covering columns — present iff the file carries them. Powers row→map zoom
  // without loading the spatial extension (no geometry decode).
  bboxes: ([number, number, number, number] | null)[];
}

// The plain numeric bbox covering columns the warehouse writes (sink_archive). Read for row→map
// zoom, hidden from the displayed table (noise) like the geometry column.
const BBOX_COLS = ["bbox_xmin", "bbox_ymin", "bbox_xmax", "bbox_ymax"];

// DuckDB type → filter UI kind. Numeric (range) vs everything else (substring). Date/time stay
// "text" — a substring on the ISO string is the most useful zero-config filter.
const colType = (duckType: string): ColType =>
  /\b(INT|DEC|DOUBLE|FLOAT|REAL|NUMERIC|HUGEINT)\b/.test(duckType.toUpperCase()) ? "number" : "text";

// WHERE clause shared by the page query + the single-row geometry fetch, so OFFSET maps to the
// same row the user sees. `columns` = the displayable (filterable) columns.
function buildWhere(columns: string[], opts: PageOpts): string {
  const clauses: string[] = [];
  const s = opts.search?.trim();
  if (s) clauses.push("(" + columns.map((c) => `CAST(${ident(c)} AS VARCHAR) ILIKE ${lit(`%${s}%`)}`).join(" OR ") + ")");
  for (const f of opts.filters ?? []) {
    if (!columns.includes(f.col)) continue;
    const cl = filterClause(f);
    if (cl) clauses.push(`(${cl})`);
  }
  return clauses.length ? ` WHERE ${clauses.join(" AND ")}` : "";
}
const buildOrder = (columns: string[], opts: PageOpts): string =>
  opts.orderBy && columns.includes(opts.orderBy)
    ? ` ORDER BY ${ident(opts.orderBy)} ${opts.desc ? "DESC" : "ASC"} NULLS LAST` : "";

// One SQL predicate from a per-column filter (empty string = no constraint).
function filterClause(f: ColFilter): string {
  const c = ident(f.col);
  if (f.kind === "number") {
    const parts: string[] = [];
    if (Number.isFinite(f.min)) parts.push(`${c} >= ${f.min}`);
    if (Number.isFinite(f.max)) parts.push(`${c} <= ${f.max}`);
    return parts.join(" AND ");
  }
  const t = f.contains.trim();
  return t ? `CAST(${c} AS VARCHAR) ILIKE ${lit(`%${t}%`)}` : "";
}

/** Server-side-style paged/sorted/filtered query over a remote GeoParquet, run entirely in
 *  DuckDB-WASM via HTTP range reads. Backs the in-page dataset explorer: COUNT(*) gives the
 *  total for pagination, then LIMIT/OFFSET/ORDER BY/WHERE fetch one page. Geometry excluded. */
export async function queryParquet(parquetUrl: string, opts: PageOpts): Promise<Page> {
  const db = await getDB();
  const conn = await db.connect();
  try {
    const src = await registerUrl(parquetUrl);
    const from = `read_parquet('${src}')`;
    const desc = await conn.query(`DESCRIBE SELECT * FROM ${from};`);
    const descRows = desc.toArray();
    const allCols = descRows.map((r) => String(r.column_name));
    const hasBbox = BBOX_COLS.every((c) => allCols.includes(c));
    // Displayed columns: drop geometry + the bbox covering columns (kept only for zoom).
    const hidden = new Set([GEOM, ...(hasBbox ? BBOX_COLS : [])]);
    const columns = allCols.filter((c) => !hidden.has(c));
    const types: Record<string, ColType> = {};
    for (const r of descRows) {
      const name = String(r.column_name);
      if (!hidden.has(name)) types[name] = colType(String(r.column_type ?? ""));
    }

    // WHERE = global free-text (OR across all columns) AND each per-column filter.
    const where = buildWhere(columns, opts);
    const totalRes = await conn.query(`SELECT count(*) AS n FROM ${from}${where};`);
    const total = Number(totalRes.toArray()[0]?.n ?? 0);

    const order = buildOrder(columns, opts);
    // Select displayed cols + bbox cols explicitly (excluding geom) so bbox survives for zoom.
    const sel = allCols.includes(GEOM) ? `* EXCLUDE (${GEOM})` : "*";
    const res = await conn.query(
      `SELECT ${sel} FROM ${from}${where}${order} LIMIT ${opts.limit} OFFSET ${opts.offset};`,
    );
    const raw = res.toArray().map((r) => r.toJSON() as Record<string, unknown>);
    const rows = raw.map((o) => {
      const out: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(o)) if (!hidden.has(k)) out[k] = sanitize(v);
      return out;
    });
    const bboxes = raw.map((o): [number, number, number, number] | null => {
      if (!hasBbox) return null;
      const b = BBOX_COLS.map((c) => Number(o[c]));
      return b.every((n) => Number.isFinite(n)) ? (b as [number, number, number, number]) : null;
    });
    return { columns, types, rows, total, bboxes };
  } finally {
    await conn.close();
  }
}

/** Real geometry of one row (the row at `rowOffset` under the same filter+sort as the page query),
 *  read as WKB and parsed to GeoJSON in JS — NO spatial extension load (which trips DuckDB-WASM's
 *  GeoParquet CRS reader). Returns null if the file has no geometry or the row is gone. */
export async function fetchGeometry(
  parquetUrl: string, opts: Omit<PageOpts, "limit" | "offset">, rowOffset: number,
): Promise<GeoJSON.Geometry | null> {
  const db = await getDB();
  const conn = await db.connect();
  try {
    const src = await registerUrl(parquetUrl);
    const from = `read_parquet('${src}')`;
    const desc = await conn.query(`DESCRIBE SELECT * FROM ${from};`);
    const allCols = desc.toArray().map((r) => String(r.column_name));
    if (!allCols.includes(GEOM)) return null;
    const hasBbox = BBOX_COLS.every((c) => allCols.includes(c));
    const columns = allCols.filter((c) => c !== GEOM && !(hasBbox && BBOX_COLS.includes(c)));
    const full: PageOpts = { ...opts, limit: 1, offset: rowOffset };
    const res = await conn.query(
      `SELECT ${ident(GEOM)} AS g FROM ${from}${buildWhere(columns, full)}${buildOrder(columns, full)} LIMIT 1 OFFSET ${rowOffset};`,
    );
    const blob = res.toArray()[0]?.g as Uint8Array | null | undefined;
    if (!blob) return null;
    const { wkbToGeoJSON } = await import("./wkb");
    return wkbToGeoJSON(blob);
  } finally {
    await conn.close();
  }
}

export async function exportItem(
  parquetUrl: string,
  stem: string,
  fmt: ExportFormat,
  clip?: [number, number, number, number], // [w,s,e,n] in 4326 — clip to this AOI
): Promise<void> {
  const duckdb = await import("@duckdb/duckdb-wasm");
  const db = await getDB();
  const conn = await db.connect();
  const id = ++seq;
  const src = `s${id}.parquet`;
  let csvOut: string | undefined;
  try {
    await db.registerFileURL(src, parquetUrl, duckdb.DuckDBDataProtocol.HTTP, false);
    // Read FIRST, before loading spatial: spatial's GeoParquet reader trips over the
    // CRS metadata ("stoi: no conversion"). Plain read already yields a GEOMETRY column.
    await conn.query(`CREATE TABLE raw AS SELECT * FROM read_parquet('${src}');`);
    await conn.query("INSTALL spatial; LOAD spatial;");
    const desc = await conn.query(`DESCRIBE raw;`);
    const geomType = String(desc.toArray().find((r) => String(r.column_name) === GEOM)?.column_type ?? "").toUpperCase();
    const geom = geomType.includes("BLOB") ? `ST_GeomFromWKB(${GEOM})` : GEOM;

    // Optional AOI clip: keep only features intersecting the bbox (features kept whole,
    // not geometrically cut — a "download what's in this area" filter).
    let t = "raw";
    if (clip) {
      const [w, s, e, n] = clip;
      await conn.query(
        `CREATE TABLE clipped AS SELECT * FROM raw WHERE ST_Intersects(${geom}, ST_MakeEnvelope(${w}, ${s}, ${e}, ${n}));`,
      );
      t = "clipped";
    }

    if (fmt === "csv") {
      csvOut = `o${id}.csv`;
      await conn.query(`COPY (SELECT * REPLACE (ST_AsText(${geom}) AS ${GEOM}) FROM ${t}) TO '${csvOut}' (HEADER, DELIMITER ',');`);
      triggerDownload(await db.copyFileToBuffer(csvOut), `${stem}.csv`, "text/csv");
      return;
    }

    // Build a GeoJSON FeatureCollection in JS (ST_AsGeoJSON for geometry).
    const res = await conn.query(`SELECT * EXCLUDE (${GEOM}), ST_AsGeoJSON(${geom}) AS __g FROM ${t};`);
    const fc = {
      type: "FeatureCollection",
      features: res.toArray().map((row) => {
        const o = row.toJSON() as Record<string, unknown>;
        const g = o.__g as string | null;
        delete o.__g;
        const properties: Record<string, unknown> = {};
        for (const [k, v] of Object.entries(o)) properties[k] = sanitize(v);
        return { type: "Feature", geometry: g ? JSON.parse(g) : null, properties };
      }),
    };
    const geojson = JSON.stringify(fc);

    if (fmt === "geojson") {
      triggerDownload(new TextEncoder().encode(geojson), `${stem}.geojson`, "application/geo+json");
      return;
    }

    // gpkg / shp / gdb / fgb via gdal3.js (~40 MB, lazy-loaded here only)
    const { convertGeoJSON, GDAL_TARGETS } = await import("./gdal");
    const { bytes, filename, mime } = await convertGeoJSON(geojson, stem, GDAL_TARGETS[fmt]);
    triggerDownload(bytes, filename, mime);
  } finally {
    await conn.query("DROP TABLE IF EXISTS raw; DROP TABLE IF EXISTS clipped;").catch(() => {});
    await conn.close();
    await db.dropFile(src).catch(() => {});
    if (csvOut) await db.dropFile(csvOut).catch(() => {});
  }
}
