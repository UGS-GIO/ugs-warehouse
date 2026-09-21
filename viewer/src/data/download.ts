// Client-side export: read an item's GeoParquet over the CDN in DuckDB-WASM, then hand
// it to gdal3.js (real GDAL/OGR in WASM) for format conversion — no server, no perms.
// DuckDB + gdal3.js load lazily on first export.
//
// Split of responsibilities:
//   - DuckDB reads the remote GeoParquet (its native CSV/JSON writers + ST_AsGeoJSON work;
//     its GDAL output drivers are broken, so we don't use those).
//   - Features leave DuckDB as newline-delimited GeoJSON *bytes* (COPY ... FORMAT JSON), never
//     as a JS string: JSON.stringify throws past ~512 MB (V8's max string), which a large topic
//     hits long before the shapefile's own 2 GB limit. The .geojson download wraps those bytes;
//     the GDAL formats read them via OGR's GeoJSONSeq driver. CSV via DuckDB's native COPY.
//   - GPKG / Shapefile / FileGDB / FlatGeobuf go through gdal3.js, whose OGR drivers
//     write these correctly (incl. Esri .gdb — OpenFileGDB write, GDAL ≥ 3.6). gdal3.js
//     is ~40 MB (wasm+data), so it's dynamically imported only when one is requested.

import { newDuckDb } from "./duckdb";
import type { ExportFormat } from "./export-formats";

export type { ExportFormat };

// The transform writes the geometry column as `geom` (GEOMETRY 4326); pub/external parquet may
// use `geometry` / `wkb_geometry`. GEOM = the canonical name (export); GEOM_NAMES = all hidden
// from the explorer table + probed for the row-geometry fetch.
const GEOM = "geom";
const GEOM_NAMES = ["geom", "geometry", "wkb_geometry"];

type DB = import("@duckdb/duckdb-wasm").AsyncDuckDB;
let dbPromise: Promise<DB> | null = null;

/** One shared DuckDB-WASM instance, on the self-hosted bundle (see data/duckdb.ts). */
async function getDB(): Promise<DB> {
  if (dbPromise) return dbPromise;
  dbPromise = newDuckDb();
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
const MAX_SAFE = BigInt(Number.MAX_SAFE_INTEGER);
export const sanitize = (v: unknown): unknown => {
  // BigInt within JS's safe range → Number; beyond it → string, so huge ids keep exact precision.
  if (typeof v === "bigint") return v <= MAX_SAFE && v >= -MAX_SAFE ? Number(v) : v.toString();
  return v instanceof Date ? v.toISOString() : v;
};

// Deterministic shapefile field-name limits (pure — no data read). Field names > 10 chars get
// truncated; two that collapse to the same 10-char name collide (silent data loss); >255 fields
// is a hard cap.
export function shapefileFieldChecks(cols: string[]): {
  longNames: string[]; collisions: [string, string][]; fieldCount: number; tooManyFields: boolean;
} {
  const longNames = cols.filter((c) => c.length > 10);
  const seen = new Map<string, string>();
  const collisions: [string, string][] = [];
  for (const c of cols) {
    const key = c.slice(0, 10).toLowerCase();
    if (seen.has(key)) collisions.push([seen.get(key)!, c]);
    else seen.set(key, c);
  }
  return { longNames, collisions, fieldCount: cols.length, tooManyFields: cols.length > 255 };
}

// ---- Newline-delimited GeoJSON ----

/** SELECT whose columns *are* a GeoJSON Feature, so DuckDB's JSON writer emits one Feature per
 *  line (GeoJSONSeq). `geometry` / `properties` are cast to JSON so they nest instead of being
 *  written as escaped strings. int64 and dates go out as JSON numbers/ISO text — no `sanitize`
 *  round-trip through JS values. */
export function featureSeqSql(cols: string[], geomExpr: string, from: string): string {
  const props = cols.length
    ? `to_json({${cols.map((c) => `${lit(c)}: ${ident(c)}`).join(", ")}})`
    : `'{}'::JSON`;
  return `SELECT 'Feature' AS type, ST_AsGeoJSON(${geomExpr})::JSON AS geometry, `
    + `${props} AS properties FROM ${from}`;
}

/** Wrap GeoJSONSeq bytes into one FeatureCollection, in place of a JS string join. A raw 0x0A
 *  only ever separates records (the writer escapes newlines inside strings), so it doubles as
 *  the comma. */
export function seqToFeatureCollection(seq: Uint8Array): Uint8Array {
  const enc = new TextEncoder();
  const head = enc.encode('{"type":"FeatureCollection","features":[');
  const tail = enc.encode("]}");
  let end = seq.length;
  while (end > 0 && seq[end - 1] === 0x0a) end--;
  const out = new Uint8Array(head.length + end + tail.length);
  out.set(head, 0);
  out.set(seq.subarray(0, end), head.length);
  for (let i = head.length, n = head.length + end; i < n; i++) if (out[i] === 0x0a) out[i] = 0x2c;
  out.set(tail, head.length + end);
  return out;
}

// ---- Shapefile size model ----

// Per-file cap: the .shp and .dbf each carry a 32-bit byte offset, so each maxes out at 2 GB
// independently. A wide attribute table can blow the .dbf while the .shp is nowhere near.
export const SHP_FILE_LIMIT = 2 * 1024 ** 3;

/** DBF field width by source type, following GDAL's shapefile writer defaults. Text has no fixed
 *  width, so it takes the column's longest decoded value (`maxBytes`), which is what the driver
 *  sizes the field to. Parquet's `total_uncompressed_size` must NOT be used for this: it is the
 *  page size after dictionary encoding, so a repeated string reports a fraction of its real width
 *  and the .dbf estimate comes out an order of magnitude low. */
export function dbfFieldWidth(type: string, maxBytes: number): number {
  const t = type.toUpperCase();
  if (t.startsWith("BOOLEAN")) return 1;
  if (/^U?(BIG|HUGE)INT/.test(t)) return 20;
  if (/^U?(TINY|SMALL|INTEGER|INT)/.test(t)) return 11;
  if (/DOUBLE|FLOAT|REAL|DECIMAL|NUMERIC/.test(t)) return 24;
  if (t.startsWith("DATE")) return 8;
  if (t.startsWith("TIMESTAMP") || t.startsWith("TIME")) return 24;
  return Math.min(254, Math.max(1, Math.ceil(maxBytes)));
}

/** True when `dbfFieldWidth` would fall through to the text branch, i.e. the column needs a
 *  measured width rather than a fixed one. */
export function needsMeasuredWidth(type: string): boolean {
  return dbfFieldWidth(type, 1) === 1 && !type.toUpperCase().startsWith("BOOLEAN");
}

/** Estimate the uncompressed .shp and .dbf from parquet footer stats — no column data read.
 *  `geomBytes` is the geometry column's uncompressed size (WKB, close to the .shp payload). */
export function estimateShapefileBytes(
  fields: { type: string; maxBytes: number }[],
  rowCount: number,
  geomBytes: number,
): { estShpBytes: number; estDbfBytes: number; over2gb: boolean } {
  const estShpBytes = 100 + geomBytes + rowCount * 40;          // 100-byte header + per-record framing
  const width = fields.reduce((n, f) => n + dbfFieldWidth(f.type, f.maxBytes), 0);
  const estDbfBytes = 33 + 32 * fields.length + rowCount * (width + 1);   // header + descriptors + rows
  return {
    estShpBytes,
    estDbfBytes,
    over2gb: estShpBytes > SHP_FILE_LIMIT || estDbfBytes > SHP_FILE_LIMIT,
  };
}

// ---- Browser memory ceiling ----

// A wasm32 module's linear memory stops at 4 GiB and browsers fail the growth well before that.
// gdal3.js is the tighter of the two instances: it holds its copy of the GeoJSONSeq input, the
// layer it writes, and (for shp/gdb) the zip, all at once. This budget is where that instance
// starts failing in practice — below the format's own 2 GB cap, so it bites first.
export const WASM_HEAP_BUDGET = 1.5 * 1024 ** 3;

/** Estimate the GeoJSONSeq fed to GDAL: coordinates go from 16 binary bytes to ~40 text
 *  characters, and every feature repeats every field name. */
export function estimateGeoJSONBytes(
  fields: { name: string; avgBytes: number }[],
  rowCount: number,
  geomBytes: number,
): number {
  const geometry = geomBytes * 2.5 + rowCount * 40;
  const perRow = fields.reduce((n, f) => n + f.name.length + Math.ceil(f.avgBytes) + 6, 0);
  return Math.round(geometry + rowCount * (60 + perRow));   // 60 = the Feature envelope
}

/** Peak bytes live in the GDAL instance at once. */
export function estimateExportPeakBytes(geojsonBytes: number, outputBytes: number): number {
  return geojsonBytes + outputBytes * 2;   // input copy + written layer + zip
}

/** Base geometry types (MULTI / Z / M stripped), deduped — >1 can't share a shapefile.
 *  GeoParquet spells the dimension with a space ("Point Z") where ST_GeometryType does not, so
 *  the suffix strip has to take the space with it or "Point Z" and "Point" stop deduping. */
export function baseGeometryTypes(names: string[]): string[] {
  return [...new Set(names.map((g) =>
    g.toUpperCase().replace(/^ST_/, "").replace(/^MULTI/, "").replace(/[\sZM]+$/, "")))];
}

/** The geometry column actually present, since pub/external parquet may not call it `geom`. */
export function geomColumn(names: string[]): string {
  return GEOM_NAMES.find((g) => names.includes(g)) ?? GEOM;
}

let seq = 0;

// Identifier / literal quoting for SQL built from column names + a user search term.
const ident = (c: string) => `"${c.replace(/"/g, '""')}"`;
const lit = (s: string) => `'${s.replace(/'/g, "''")}'`;

// Register each remote GeoParquet with DuckDB-WASM ONCE, reuse across paged queries — DuckDB
// pulls only the footer + needed row-groups per query over HTTP range reads, so paging a 7000-row
// table never downloads the whole file. Cache keyed by URL so the explorer's page/sort/search
// re-queries hit the same registered handle.
// LRU of registered parquet handles; each pins DuckDB's in-WASM HTTP buffer, so cap + dropFile the
// LRU so browsing many tables can't grow the heap unbounded. Re-insert on hit = MRU (insertion order).
const REGISTERED_CAP = 12;
const registered = new Map<string, string>();

// Ref-count in-use handles so eviction never dropFiles one a live query is mid-read on. Callers
// borrow via registerUrl and release() in finally.
const inUse = new Map<string, number>();
const borrow = (src: string) => inUse.set(src, (inUse.get(src) ?? 0) + 1);
const release = (src: string) => { const n = (inUse.get(src) ?? 0) - 1; if (n > 0) inUse.set(src, n); else inUse.delete(src); };

/** Oldest (LRU) registered entry whose handle is NOT currently in use, or undefined if all are.
 *  Pure so the eviction policy is unit-tested — see download.test.ts. */
export function evictionVictim(
  registered: Map<string, string>, borrowed: Map<string, number>,
): [string, string] | undefined {
  for (const entry of registered) if (!borrowed.has(entry[1])) return entry;
  return undefined;
}

async function registerUrl(parquetUrl: string): Promise<string> {
  const hit = registered.get(parquetUrl);
  if (hit) { registered.delete(parquetUrl); registered.set(parquetUrl, hit); borrow(hit); return hit; }  // touch → MRU
  const duckdb = await import("@duckdb/duckdb-wasm");
  const db = await getDB();
  // Evict the LRU, skipping in-use handles; if all are borrowed, run temporarily over cap.
  while (registered.size >= REGISTERED_CAP) {
    const victim = evictionVictim(registered, inUse);
    if (!victim) break;
    registered.delete(victim[0]);
    try { await db.dropFile(victim[1]); } catch { /* already gone — best-effort */ }
  }
  const src = `q${++seq}.parquet`;
  await db.registerFileURL(src, parquetUrl, duckdb.DuckDBDataProtocol.HTTP, false);
  registered.set(parquetUrl, src);
  borrow(src);  // returned handle is borrowed; caller MUST release() in finally
  return src;
}

export type ColType = "number" | "text";
// Per-column filter: numeric columns get a range (min/max), everything else a substring match.
export type ColFilter =
  | { col: string; kind: "number"; min?: number; max?: number }
  | { col: string; kind: "text"; contains: string }
  // Exact equality — the related-table join (child.fk = the clicked feature's key value).
  | { col: string; kind: "exact"; value: string };

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

// Stable per-row id the transform stamps (1..N, hilbert order) into BOTH the GeoParquet and the
// PMTiles (as the MVT feature id). It's the join key for map↔table linking. Hidden from the
// displayed columns (synthetic noise) but kept on each row object so the table can highlight a
// row the map picked, and used as a deterministic ORDER BY tiebreaker under a user sort so OFFSET
// paging and the feature_id→ordinal lookup agree exactly. Because it is stamped in hilbert order,
// it also matches file order — which is why the unsorted page can drop the sort entirely.
const ID_COL = "feature_id";

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
// Inner ORDER BY expression (no leading " ORDER BY "): the user's sort, then feature_id as a stable
// tiebreaker so two rows with an equal sort key always page in the same order — and the
// feature_id→ordinal lookup (a row_number() window over this same expression) lines up with
// LIMIT/OFFSET paging exactly. `hasId` is false for pre-reingest parquet with no feature_id.
//
// Empty with NO user sort, on purpose: a sort is global, so `ORDER BY feature_id` alone made the
// unsorted first page read every column chunk in the file (11.3 MB on wetlands_riverine) before
// returning row 1. feature_id is stamped in file order, so the tiebreaker bought nothing there.
function orderExpr(columns: string[], opts: PageOpts, hasId: boolean): string {
  const sorted = opts.orderBy && columns.includes(opts.orderBy);
  if (!sorted) return "";
  const parts = [`${ident(opts.orderBy!)} ${opts.desc ? "DESC" : "ASC"} NULLS LAST`];
  if (hasId) parts.push(`${ident(ID_COL)} ASC`);
  return parts.join(", ");
}
export const buildOrder = (columns: string[], opts: PageOpts, hasId: boolean): string => {
  const e = orderExpr(columns, opts, hasId);
  return e ? ` ORDER BY ${e}` : "";
};

// One SQL predicate from a per-column filter (empty string = no constraint).
export function filterClause(f: ColFilter): string {
  const c = ident(f.col);
  if (f.kind === "number") {
    const parts: string[] = [];
    if (Number.isFinite(f.min)) parts.push(`${c} >= ${f.min}`);
    if (Number.isFinite(f.max)) parts.push(`${c} <= ${f.max}`);
    return parts.join(" AND ");
  }
  // Compare as text so the join works whether the key column is numeric or string.
  if (f.kind === "exact") return `CAST(${c} AS VARCHAR) = ${lit(f.value)}`;
  const t = f.contains.trim();
  return t ? `CAST(${c} AS VARCHAR) ILIKE ${lit(`%${t}%`)}` : "";
}

/** Server-side-style paged/sorted/filtered query over a remote GeoParquet, run entirely in
 *  DuckDB-WASM via HTTP range reads. Backs the in-page dataset explorer: COUNT(*) gives the
 *  total for pagination, then LIMIT/OFFSET/ORDER BY/WHERE fetch one page. Geometry excluded. */
/** Displayed column → filter kind, from the schema alone — no page needed. */
export async function columnTypes(parquetUrl: string): Promise<Record<string, ColType>> {
  const db = await getDB();
  const conn = await db.connect();
  let borrowed: string | undefined;
  try {
    const src = borrowed = await registerUrl(parquetUrl);
    const desc = await conn.query(`DESCRIBE SELECT * FROM read_parquet('${src}');`);
    return typesOf(desc.toArray());
  } finally {
    if (borrowed !== undefined) release(borrowed);
    await conn.close();
  }
}

function typesOf(descRows: { column_name?: unknown; column_type?: unknown }[]): Record<string, ColType> {
  const allCols = descRows.map((r) => String(r.column_name));
  const geomCols = GEOM_NAMES.filter((c) => allCols.includes(c));
  const hasBbox = BBOX_COLS.every((c) => allCols.includes(c));
  const hidden = new Set([...geomCols, ...(hasBbox ? BBOX_COLS : []), ID_COL]);
  const types: Record<string, ColType> = {};
  for (const r of descRows) {
    const name = String(r.column_name);
    if (!hidden.has(name)) types[name] = colType(String(r.column_type ?? ""));
  }
  return types;
}

export async function queryParquet(parquetUrl: string, opts: PageOpts): Promise<Page> {
  const db = await getDB();
  const conn = await db.connect();
  let borrowed: string | undefined;  // released in finally so LRU eviction can't drop a live handle
  try {
    const src = borrowed = await registerUrl(parquetUrl);
    const from = `read_parquet('${src}')`;
    const desc = await conn.query(`DESCRIBE SELECT * FROM ${from};`);
    const descRows = desc.toArray();
    const allCols = descRows.map((r) => String(r.column_name));
    const hasBbox = BBOX_COLS.every((c) => allCols.includes(c));
    const hasId = allCols.includes(ID_COL);
    const geomCols = GEOM_NAMES.filter((c) => allCols.includes(c));
    // Row object drops geometry + bbox (noise) but KEEPS feature_id so the table can highlight a
    // map-picked row. Displayed columns additionally drop feature_id (synthetic, not user data).
    const rowHidden = new Set([...geomCols, ...(hasBbox ? BBOX_COLS : [])]);
    const colHidden = new Set([...rowHidden, ID_COL]);
    const columns = allCols.filter((c) => !colHidden.has(c));
    const types = typesOf(descRows);

    // WHERE = global free-text (OR across all columns) AND each per-column filter.
    const where = buildWhere(columns, opts);
    const totalRes = await conn.query(`SELECT count(*) AS n FROM ${from}${where};`);
    const total = Number(totalRes.toArray()[0]?.n ?? 0);

    const order = buildOrder(columns, opts, hasId);
    // Select displayed cols + bbox cols explicitly (excluding geometry) so bbox survives for zoom.
    const sel = geomCols.length ? `* EXCLUDE (${geomCols.map(ident).join(", ")})` : "*";
    const res = await conn.query(
      `SELECT ${sel} FROM ${from}${where}${order} LIMIT ${opts.limit} OFFSET ${opts.offset};`,
    );
    const raw = res.toArray().map((r) => r.toJSON() as Record<string, unknown>);
    const rows = raw.map((o) => {
      const out: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(o)) if (!rowHidden.has(k)) out[k] = sanitize(v);
      return out;
    });
    const bboxes = raw.map((o): [number, number, number, number] | null => {
      if (!hasBbox) return null;
      const b = BBOX_COLS.map((c) => Number(o[c]));
      return b.every((n) => Number.isFinite(n)) ? (b as [number, number, number, number]) : null;
    });
    return { columns, types, rows, total, bboxes };
  } finally {
    if (borrowed !== undefined) release(borrowed);
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
  let borrowed: string | undefined;  // released in finally so LRU eviction can't drop a live handle
  try {
    const src = borrowed = await registerUrl(parquetUrl);
    const from = `read_parquet('${src}')`;
    const desc = await conn.query(`DESCRIBE SELECT * FROM ${from};`);
    const allCols = desc.toArray().map((r) => String(r.column_name));
    const geomCol = GEOM_NAMES.find((c) => allCols.includes(c));
    if (!geomCol) return null;
    const hasBbox = BBOX_COLS.every((c) => allCols.includes(c));
    const hasId = allCols.includes(ID_COL);
    const hidden = new Set([...GEOM_NAMES, ...(hasBbox ? BBOX_COLS : []), ID_COL]);
    const columns = allCols.filter((c) => !hidden.has(c));
    const full: PageOpts = { ...opts, limit: 1, offset: rowOffset };
    const res = await conn.query(
      `SELECT ${ident(geomCol)} AS g FROM ${from}${buildWhere(columns, full)}${buildOrder(columns, full, hasId)} LIMIT 1 OFFSET ${rowOffset};`,
    );
    const blob = res.toArray()[0]?.g as Uint8Array | null | undefined;
    if (!blob) return null;
    const { wkbToGeoJSON } = await import("./wkb");
    return wkbToGeoJSON(blob);
  } finally {
    if (borrowed !== undefined) release(borrowed);
    await conn.close();
  }
}

/** 0-based position of the row carrying `featureId` under the SAME sort+filter the explorer shows,
 *  so the caller can jump the table to page `floor(pos / PAGE_SIZE)` and highlight row
 *  `pos % PAGE_SIZE`. Returns null if the parquet has no feature_id, or the row is filtered out of
 *  the current view. Uses the same orderExpr (incl. the feature_id tiebreaker) as the page query,
 *  so the position aligns with OFFSET paging exactly. */
export async function ordinalByFeatureId(
  parquetUrl: string, featureId: number, opts: Omit<PageOpts, "limit" | "offset">,
): Promise<number | null> {
  const db = await getDB();
  const conn = await db.connect();
  let borrowed: string | undefined;  // released in finally so LRU eviction can't drop a live handle
  try {
    const src = borrowed = await registerUrl(parquetUrl);
    const from = `read_parquet('${src}')`;
    const desc = await conn.query(`DESCRIBE SELECT * FROM ${from};`);
    const allCols = desc.toArray().map((r) => String(r.column_name));
    if (!allCols.includes(ID_COL)) return null;
    const hasBbox = BBOX_COLS.every((c) => allCols.includes(c));
    const colHidden = new Set([...GEOM_NAMES, ...(hasBbox ? BBOX_COLS : []), ID_COL]);
    const columns = allCols.filter((c) => !colHidden.has(c));
    const full: PageOpts = { ...opts, limit: 1, offset: 0 };
    const where = buildWhere(columns, full);
    const ord = orderExpr(columns, full, true);  // hasId known true here
    // Empty when unsorted — the page query is then in file order, so the window must be too
    // (`OVER ()` numbers rows in scan order, which DuckDB preserves).
    const over = ord ? `OVER (ORDER BY ${ord})` : "OVER ()";
    const res = await conn.query(
      `SELECT pos FROM (
         SELECT ${ident(ID_COL)} AS fid, row_number() ${over} - 1 AS pos
         FROM ${from}${where}
       ) WHERE fid = ${Number(featureId)};`,
    );
    const pos = res.toArray()[0]?.pos;
    return pos == null ? null : Number(pos);
  } finally {
    if (borrowed !== undefined) release(borrowed);
    await conn.close();
  }
}

/** Geometry + bbox of the row carrying `featureId`, looked up directly by id (independent of the
 *  current sort/filter) so a map click can highlight + fly to the real feature even when it's
 *  filtered out of the visible table page. bbox comes from the plain covering columns (no spatial
 *  extension); geometry from the WKB blob parsed in JS. Returns null if there's no feature_id. */
export async function fetchRowById(
  parquetUrl: string, featureId: number,
): Promise<{ bbox?: [number, number, number, number]; geometry: GeoJSON.Geometry | null } | null> {
  const db = await getDB();
  const conn = await db.connect();
  let borrowed: string | undefined;  // released in finally so LRU eviction can't drop a live handle
  try {
    const src = borrowed = await registerUrl(parquetUrl);
    const from = `read_parquet('${src}')`;
    const desc = await conn.query(`DESCRIBE SELECT * FROM ${from};`);
    const allCols = desc.toArray().map((r) => String(r.column_name));
    if (!allCols.includes(ID_COL)) return null;
    const geomCol = GEOM_NAMES.find((c) => allCols.includes(c));
    const hasBbox = BBOX_COLS.every((c) => allCols.includes(c));
    const sel = [
      geomCol ? `${ident(geomCol)} AS g` : "NULL AS g",
      ...(hasBbox ? BBOX_COLS.map((c) => `${ident(c)} AS ${c}`) : []),
    ].join(", ");
    const res = await conn.query(
      `SELECT ${sel} FROM ${from} WHERE ${ident(ID_COL)} = ${Number(featureId)} LIMIT 1;`,
    );
    const row = res.toArray()[0];
    if (!row) return null;
    let geometry: GeoJSON.Geometry | null = null;
    const blob = row.g as Uint8Array | null | undefined;
    if (blob) geometry = (await import("./wkb")).wkbToGeoJSON(blob);
    let bbox: [number, number, number, number] | undefined;
    if (hasBbox) {
      const b = BBOX_COLS.map((c) => Number(row[c]));
      if (b.every((n) => Number.isFinite(n))) bbox = b as [number, number, number, number];
    }
    return { bbox, geometry };
  } finally {
    if (borrowed !== undefined) release(borrowed);
    await conn.close();
  }
}

/** Read every row of a (small) 3D GeoParquet as {props, geometry} with **Z retained** — for the 3D
 *  fence viewer, which needs the whole file (not paged) and the elevation ordinate. No spatial
 *  extension (geometry read as WKB BLOB, parsed in JS). props = all non-geometry columns. */
export async function readFeatures3D(
  parquetUrl: string,
  signal?: AbortSignal,
): Promise<Array<{ props: Record<string, unknown>; geometry: GeoJSON.Geometry | null }>> {
  const db = await getDB();
  const conn = await db.connect();
  let borrowed: string | undefined;  // released in finally so LRU eviction can't drop a live handle
  try {
    const src = borrowed = await registerUrl(parquetUrl);
    const from = `read_parquet('${src}')`;
    const desc = await conn.query(`DESCRIBE SELECT * FROM ${from};`);
    const allCols = desc.toArray().map((r) => String(r.column_name));
    const geomCol = GEOM_NAMES.find((c) => allCols.includes(c));
    const propCols = allCols.filter((c) => c !== geomCol);
    // DuckDB range reads aren't AbortSignal-cancelable, but if the fence viewer already unmounted we
    // can skip the whole-file SELECT + WKB parse (the expensive part) rather than do it for nothing.
    if (signal?.aborted) return [];
    const res = await conn.query(`SELECT * FROM ${from};`);
    const { wkbToGeoJSON } = await import("./wkb");
    return res.toArray().map((r) => {
      const o = r.toJSON() as Record<string, unknown>;
      const props: Record<string, unknown> = {};
      for (const c of propCols) props[c] = o[c];
      const blob = geomCol ? (o[geomCol] as Uint8Array | null | undefined) : undefined;
      return { props, geometry: blob ? wkbToGeoJSON(blob, true) : null };
    });
  } finally {
    if (borrowed !== undefined) release(borrowed);
    await conn.close();
  }
}

export async function exportItem(
  parquetUrl: string,
  stem: string,
  fmt: ExportFormat,
  clip?: [number, number, number, number], // [w,s,e,n] in 4326 — clip to this AOI
  epsg = 4326,                              // output CRS for the gdal formats (shp/gpkg/gdb/fgb)
): Promise<void> {
  const duckdb = await import("@duckdb/duckdb-wasm");
  const db = await getDB();
  const conn = await db.connect();
  const id = ++seq;
  const src = `s${id}.parquet`;
  let csvOut: string | undefined;
  let seqOut: string | undefined;
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
      // WKT geometry in the chosen output CRS (source is always 4326); attributes unchanged.
      // always_xy: geom is stored lon/lat, but EPSG:4326's authority axis order is lat/lon — without
      // this the transform reads longitude as latitude and returns inf.
      const wkt = epsg === 4326 ? `ST_AsText(${geom})`
        : `ST_AsText(ST_Transform(${geom}, 'EPSG:4326', 'EPSG:${epsg}', always_xy := true))`;
      await conn.query(`COPY (SELECT * REPLACE (${wkt} AS ${GEOM}) FROM ${t}) TO '${csvOut}' (HEADER, DELIMITER ',');`);
      triggerDownload(await db.copyFileToBuffer(csvOut), `${stem}.csv`, "text/csv");
      return;
    }

    // One Feature per line, written straight to a DuckDB file — the features never exist as a
    // JS value or string, so peak memory is one buffer instead of Arrow rows + string + MEMFS copy.
    const descRows = desc.toArray();
    const cols = descRows.map((r) => String(r.column_name)).filter((c) => !GEOM_NAMES.includes(c));
    seqOut = `o${id}.geojsonl`;
    await conn.query(`COPY (${featureSeqSql(cols, geom, t)}) TO '${seqOut}' (FORMAT JSON, ARRAY false);`);
    const seqBytes = await db.copyFileToBuffer(seqOut);

    if (fmt === "geojson") {
      triggerDownload(seqToFeatureCollection(seqBytes), `${stem}.geojson`, "application/geo+json");
      return;
    }

    // gpkg / shp / gdb / fgb via gdal3.js (~40 MB, lazy-loaded here only).
    const { convertFeatureSeq, GDAL_TARGETS } = await import("./gdal");
    const { bytes, filename, mime } = await convertFeatureSeq(seqBytes, stem, GDAL_TARGETS[fmt], epsg);
    triggerDownload(bytes, filename, mime);
  } finally {
    await conn.query("DROP TABLE IF EXISTS raw; DROP TABLE IF EXISTS clipped;").catch(() => {});
    await conn.close();
    await db.dropFile(src).catch(() => {});
    if (csvOut) await db.dropFile(csvOut).catch(() => {});
    if (seqOut) await db.dropFile(seqOut).catch(() => {});
  }
}

// ---- Shapefile pre-flight ----
// The Esri Shapefile format silently mangles data past its limits; check before exporting so the
// user isn't handed a broken file. The detailed checks read the parquet footer (column stats, row
// count, GeoParquet metadata) rather than any column data — but see PREFLIGHT_MAX_PARQUET_BYTES:
// reaching the footer at all costs the whole file, so a Content-Length gate comes first.
export interface ShapefileWarnings {
  longNames: string[];                 // > 10 chars → truncated by the driver
  collisions: [string, string][];      // fields that collapse to the same 10-char name → data loss
  fieldCount: number;                  // shapefile hard cap is 255
  tooManyFields: boolean;
  mixedGeometry: string[];             // >1 base geometry type → shapefile can't hold them together
  rowCount: number;
  estShpBytes: number;                 // estimated uncompressed .shp (geometry)
  estDbfBytes: number;                 // estimated uncompressed .dbf (attributes)
  over2gb: boolean;                    // either file over the format's per-file 2 GB cap
  estPeakBytes: number;                // peak bytes gdal3.js holds during the conversion
  overBrowserLimit: boolean;           // conversion won't fit in the wasm heap, whatever the format
  sourceBytes: number;                 // Content-Length of the source GeoParquet
  tooBigToInspect: boolean;            // refused on size alone; the detailed fields are unset
  any: boolean;                        // true if anything worth warning about
}

/** Geometry types off the GeoParquet `geo` key — present on anything our transform wrote, and
 *  free (footer only). Null when the file predates it or carries no type list. */
function geoMetadataTypes(json: string, geom: string): string[] | null {
  try {
    const geo = JSON.parse(json) as {
      primary_column?: string;
      columns?: Record<string, { geometry_types?: string[] }>;
    };
    const col = geo.columns?.[geo.primary_column ?? geom] ?? Object.values(geo.columns ?? {})[0];
    const types = col?.geometry_types;
    return types?.length ? types : null;
  } catch { return null; }
}

// DuckDB-WASM fetches a parquet in FULL on first access — it does not range-read, whether the file
// is registered or queried by URL (measured: first footer query costs the whole file at ~21 MB/s,
// 63s for a 1.35 GB topic, and a 2 GB one crashes the tab). So reading the footer means downloading
// everything. Past this size the answer is already "too big to convert here", and inspecting would
// mean downloading a file we are about to refuse; under it, the detailed checks cost the same
// download the export itself needs.
export const PREFLIGHT_MAX_PARQUET_BYTES = 128 * 1024 ** 2;

const UNINSPECTED = {
  longNames: [], collisions: [], fieldCount: 0, tooManyFields: false, mixedGeometry: [],
  rowCount: 0, estShpBytes: 0, estDbfBytes: 0, over2gb: false, estPeakBytes: 0,
} satisfies Partial<ShapefileWarnings>;

/** Pre-flight an export. The size limits apply to every format: all of them read the whole
 *  GeoParquet into the tab, and the four GDAL ones then run it through the same wasm instance.
 *  The shapefile-only findings (field names, field count, single geometry type, the 2 GB per-file
 *  cap) are filled in for `shp` alone. */
export async function exportWarnings(
  parquetUrl: string,
  fmt: ExportFormat,
  clip?: [number, number, number, number],
): Promise<ShapefileWarnings> {
  const shp = fmt === "shp";
  let sourceBytes = 0;
  try {
    const head = await fetch(parquetUrl, { method: "HEAD" });
    sourceBytes = Number(head.headers.get("content-length")) || 0;
  } catch { /* HEAD blocked → no gate, fall through and inspect */ }
  if (sourceBytes > PREFLIGHT_MAX_PARQUET_BYTES) {
    return { ...UNINSPECTED, sourceBytes, tooBigToInspect: true, overBrowserLimit: true, any: true };
  }

  const duckdb = await import("@duckdb/duckdb-wasm");
  const db = await getDB();
  const conn = await db.connect();
  const id = ++seq;
  const src = `chk${id}.parquet`;
  try {
    await db.registerFileURL(src, parquetUrl, duckdb.DuckDBDataProtocol.HTTP, false);
    const from = `read_parquet('${src}')`;
    const desc = (await conn.query(`DESCRIBE SELECT * FROM ${from};`)).toArray();
    const colTypes = new Map(desc.map((r) => [String(r.column_name), String(r.column_type)]));
    const geom = geomColumn([...colTypes.keys()]);
    const geomExpr = String(colTypes.get(geom) ?? "").toUpperCase().includes("BLOB")
      ? `ST_GeomFromWKB(${ident(geom)})` : ident(geom);
    const cols = [...colTypes.keys()].filter((c) => !GEOM_NAMES.includes(c));

    // Field-name limits (10 chars) + post-truncation collisions (pure, unit-tested).
    const { longNames, collisions } = shp
      ? shapefileFieldChecks(cols)
      : { longNames: [] as string[], collisions: [] as [string, string][] };

    // Per-column uncompressed bytes + the row count, both from the footer.
    const meta = (await conn.query(
      `SELECT path_in_schema AS c, sum(total_uncompressed_size)::BIGINT AS b
       FROM parquet_metadata('${src}') GROUP BY 1;`,
    )).toArray();
    const colBytes = new Map(meta.map((r) => [String(r.c), Number(r.b)]));
    const fullRows = Number((await conn.query(
      `SELECT sum(num_rows)::BIGINT AS n FROM parquet_file_metadata('${src}');`,
    )).toArray()[0].n);

    // A clip only scales the estimate. The per-row bbox columns answer that without decoding
    // geometry (and prune row groups); fall back to a real intersect if the file lacks them.
    let where = "";
    if (clip) {
      const [w, sy, e, n] = clip;
      if (BBOX_COLS.every((c) => colTypes.has(c))) {
        where = ` WHERE bbox_xmin <= ${e} AND bbox_xmax >= ${w} AND bbox_ymin <= ${n} AND bbox_ymax >= ${sy}`;
      } else {
        await conn.query("INSTALL spatial; LOAD spatial;");
        where = ` WHERE ST_Intersects(${geomExpr}, ST_MakeEnvelope(${w}, ${sy}, ${e}, ${n}))`;
      }
    }
    const rowCount = where
      ? Number((await conn.query(`SELECT count(*) AS n FROM ${from}${where};`)).toArray()[0].n)
      : fullRows;

    // Geometry types. The GeoParquet `geo` key is file-level, so under a clip it would report
    // types the selection no longer holds; scan the column instead whenever the user clipped.
    let names: string[] | null = null;
    if (!where) {
      const kv = (await conn.query(
        `SELECT decode(value) AS v FROM parquet_kv_metadata('${src}') WHERE decode(key) = 'geo';`,
      )).toArray();
      if (kv.length) names = geoMetadataTypes(String(kv[0].v), geom);
    }
    if (!names) {
      await conn.query("INSTALL spatial; LOAD spatial;");
      names = (await conn.query(
        `SELECT DISTINCT ST_GeometryType(${geomExpr}) AS g FROM ${from}${where}
         ${where ? "AND" : "WHERE"} ${geomExpr} IS NOT NULL;`,
      )).toArray().map((r) => String(r.g));
    }
    const baseTypes = baseGeometryTypes(names);

    // Text columns need their decoded length, which the footer cannot give (see dbfFieldWidth).
    // The file is already local by now — reaching the footer downloaded all of it — so the scan
    // costs no extra transfer.
    const measured = cols.filter((c) => needsMeasuredWidth(colTypes.get(c) ?? "VARCHAR"));
    const lengths = new Map<string, { max: number; avg: number }>();
    if (measured.length) {
      // strlen is the BYTE count (length would count characters, and the dbf pads bytes).
      const sel = measured.map((c, i) =>
        `max(strlen(CAST(${ident(c)} AS VARCHAR))) AS m${i}, `
        + `avg(strlen(CAST(${ident(c)} AS VARCHAR))) AS a${i}`).join(", ");
      const row = (await conn.query(`SELECT ${sel} FROM ${from}${where};`)).toArray()[0];
      measured.forEach((c, i) =>
        lengths.set(c, { max: Number(row[`m${i}`] ?? 0), avg: Number(row[`a${i}`] ?? 0) }));
    }

    const scale = fullRows ? rowCount / fullRows : 0;
    const fields = cols.map((c) => {
      const type = colTypes.get(c) ?? "VARCHAR";
      const m = lengths.get(c);
      const fixed = dbfFieldWidth(type, 0);
      return { name: c, type, maxBytes: m ? m.max : fixed, avgBytes: m ? m.avg : fixed };
    });
    const geomBytes = Math.round((colBytes.get(geom) ?? 0) * scale);
    const { estShpBytes, estDbfBytes, over2gb } = estimateShapefileBytes(fields, rowCount, geomBytes);
    const estPeakBytes = estimateExportPeakBytes(
      estimateGeoJSONBytes(fields, rowCount, geomBytes), estShpBytes + estDbfBytes);
    const overBrowserLimit = estPeakBytes > WASM_HEAP_BUDGET;

    const tooManyFields = shp && cols.length > 255;
    const mixedGeometry = shp && baseTypes.length > 1 ? baseTypes : [];
    return {
      longNames, collisions, fieldCount: cols.length, tooManyFields,
      mixedGeometry, rowCount, estShpBytes, estDbfBytes, over2gb: shp && over2gb,
      estPeakBytes, overBrowserLimit, sourceBytes, tooBigToInspect: false,
      any: longNames.length > 0 || collisions.length > 0 || tooManyFields
        || mixedGeometry.length > 0 || (shp && over2gb) || overBrowserLimit,
    };
  } finally {
    await conn.close();
    await db.dropFile(src).catch(() => {});
  }
}
