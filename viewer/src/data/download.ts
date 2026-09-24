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

import { BBOX_COLS, COVERING_COL, GEOM_NAMES, ID_COL, sanitize } from "./columns";
import { newDuckDb } from "./duckdb";
import {
  beginExport, consumeIfCancelled, endRun, isCancelled, startRun,
} from "./export-runs";
import type { ExportFormat } from "./export-formats";

export type { ExportFormat };
export {
  beginExport, cancelExport, currentExports, endRun, startRun, subscribeExport, type ExportRun,
} from "./export-runs";

// The transform writes the geometry column as `geom` (GEOMETRY 4326); pub/external parquet may
// use `geometry` / `wkb_geometry`. GEOM = the canonical name (export); GEOM_NAMES = all hidden
// from the explorer table + probed for the row-geometry fetch.
const GEOM = "geom";

type DB = import("@duckdb/duckdb-wasm").AsyncDuckDB;
let dbPromise: Promise<DB> | null = null;

/** One shared DuckDB-WASM instance, on the self-hosted bundle (see data/duckdb.ts). */
async function getDB(): Promise<DB> {
  if (dbPromise) return dbPromise;
  dbPromise = newDuckDb();
  return dbPromise;
}

/** Blob rejects a SharedArrayBuffer view and DuckDB's buffers can be one. Copy only then: at
 *  export sizes a blanket copy is a second full allocation. */
function blobSafe(bytes: Uint8Array): Uint8Array<ArrayBuffer> {
  if (typeof SharedArrayBuffer !== "undefined" && bytes.buffer instanceof SharedArrayBuffer) {
    const copy = new Uint8Array(bytes.byteLength);
    copy.set(bytes);
    return copy;
  }
  return bytes as Uint8Array<ArrayBuffer>;   // the check above is what rules out the shared case
}

function triggerDownload(parts: Uint8Array[], filename: string, mime: string): void {
  const url = URL.createObjectURL(new Blob(parts.map(blobSafe), { type: mime }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export { sanitize };

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

/** Wrap GeoJSONSeq bytes into one FeatureCollection, in place of a JS string join, returning Blob
 *  parts. MUTATES `seq`: a raw 0x0A only ever separates records (the writer escapes newlines
 *  inside strings), so the separators are overwritten with commas where they sit. A second copy
 *  of the payload is the one allocation a large GeoJSON export cannot afford. */
export function wrapFeatureSeqInPlace(seq: Uint8Array): Uint8Array[] {
  const enc = new TextEncoder();
  let end = seq.length;
  while (end > 0 && seq[end - 1] === 0x0a) end--;
  for (let i = 0; i < end; i++) if (seq[i] === 0x0a) seq[i] = 0x2c;
  return [enc.encode('{"type":"FeatureCollection","features":['), seq.subarray(0, end), enc.encode("]}")];
}

// ---- Shapefile size model ----

// Per-file cap: the .shp and .dbf each carry a 32-bit byte offset, so each maxes out at 2 GB
// independently. A wide attribute table can blow the .dbf while the .shp is nowhere near.
export const SHP_FILE_LIMIT = 2 * 1024 ** 3;

/** DBF field width, following GDAL's shapefile writer defaults. Text takes the column's longest
 *  decoded value. NOT parquet's `total_uncompressed_size`: that is post-dictionary-encoding, so a
 *  repeated string reports a fraction of its width and the estimate lands an order low. */
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

/** Peak bytes live at once for `fmt`. CSV streams from DuckDB and touches neither GeoJSON nor
 *  GDAL; the zipped formats (shp, gdb) also hold the archive. */
export function estimateExportPeakBytes(
  fmt: ExportFormat, geojsonBytes: number, outputBytes: number,
): number {
  if (fmt === "csv") return 0;
  if (fmt === "geojson") return geojsonBytes * 2;              // the seq bytes + the wrapped copy
  const zipped = fmt === "shp" || fmt === "gdb";
  return geojsonBytes + outputBytes * (zipped ? 2 : 1);
}

/** Base geometry types (MULTI / Z / M stripped), deduped — >1 can't share a shapefile.
 *  GeoParquet spells the dimension with a space ("Point Z") where ST_GeometryType does not, so
 *  the suffix strip has to take the space with it or "Point Z" and "Point" stop deduping. */
export function baseGeometryTypes(names: string[]): string[] {
  return [...new Set(names.map((g) =>
    g.toUpperCase().replace(/^ST_/, "").replace(/^MULTI/, "").replace(/[\sZM]+$/, "")))];
}

export type RowGroup = { bytes: number; xmin: number; xmax: number; ymin: number; ymax: number };

/** Bytes the export will actually fetch. A clip only reads the row groups whose extent it
 *  overlaps, so the saving depends on how finely the file is grouped, not on the AOI's size:
 *  a file written as one big group is read whole however small the area. */
export function estimateReadBytes(groups: RowGroup[], clip?: [number, number, number, number]): number {
  if (!clip) return groups.reduce((n, g) => n + g.bytes, 0);
  const [w, s, e, n] = clip;
  return groups
    .filter((g) => g.xmin <= e && g.xmax >= w && g.ymin <= n && g.ymax >= s)
    .reduce((acc, g) => acc + g.bytes, 0);
}

/** Formats that reject a layer carrying more than one base geometry type. Measured by converting
 *  a point, a linestring and a polygon through each real driver: these two fail the conversion
 *  outright, while GeoPackage and FlatGeobuf write all three. */
export const holdsOneGeomType = (fmt: ExportFormat): boolean => fmt === "shp" || fmt === "gdb";

/** Clearing the custom EPSG input yields Number("") === 0, which would reach SQL as EPSG:0. */
export const safeEpsg = (n: number): number => (Number.isInteger(n) && n > 0 ? n : 4326);

/** The geometry column actually present, since pub/external parquet may not call it `geom`. */
export function geomColumn(names: string[]): string {
  return GEOM_NAMES.find((g) => names.includes(g)) ?? GEOM;
}

let seq = 0;

// Identifier / literal quoting for SQL built from column names + a user search term.
const ident = (c: string) => `"${c.replace(/"/g, '""')}"`;
const lit = (s: string) => `'${s.replace(/'/g, "''")}'`;

// Register each remote GeoParquet with DuckDB-WASM ONCE, reuse across paged queries. With range reads
// enabled at db.open() (see data/duckdb.ts), DuckDB pulls only the footer + the projected columns'
// chunks per query, so paging a 400k-row, multi-GB table never downloads the whole file. Cache keyed
// by URL so the explorer's page/sort/search re-queries hit the same registered handle.
// LRU of registered handles: cap + dropFile so browsing many tables can't grow the heap unbounded.
// Re-insert on hit = MRU (insertion order).
const REGISTERED_CAP = 12;
const registered = new Map<string, string>();

// Ref-count in-use handles so eviction never dropFiles one a live query is mid-read on. Callers
// borrow via registerUrl and release() in finally.
const inUse = new Map<string, number>();
// In-flight registrations, so concurrent callers (columnTypes + queryParquet on mount) share ONE
// registerFileURL instead of each registering (and opening, i.e. fetching) the same URL twice.
const pending = new Map<string, { promise: Promise<string>; waiters: number }>();
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
  let p = pending.get(parquetUrl);
  if (!p) {
    const entry = { waiters: 0, promise: undefined as unknown as Promise<string> };
    entry.promise = register(parquetUrl, entry).finally(() => pending.delete(parquetUrl));
    pending.set(parquetUrl, entry);
    p = entry;
  }
  p.waiters++;  // register() borrows once per waiter before it resolves; each caller still release()s in finally
  return p.promise;
}

// Registration proper (one per URL; concurrent callers share it via `pending`). HTTP range reads are
// enabled at db.open() (see data/duckdb.ts), NOT here; directIO stays false so DuckDB's page cache
// keeps the file open across a table's paged queries. Borrows once per waiter inside the same sync
// block as the registered.set, so eviction can't drop the fresh handle before its callers mark it in use.
async function register(parquetUrl: string, entry: { waiters: number }): Promise<string> {
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
  for (let i = 0; i < entry.waiters; i++) borrow(src);
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
  // Only rows whose bbox overlaps one of these areas. Offline, a table saved by area has only those
  // rows' row groups on the device, and this is what keeps DuckDB from reading any other.
  clip?: [number, number, number, number][];
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

/** Rows whose bbox overlaps `area`, by the per-row bbox columns. Their row-group stats let DuckDB
 *  skip every row group outside it without reading it. */
export function clipClause([w, s, e, n]: [number, number, number, number]): string {
  return `(bbox_xmin <= ${e} AND bbox_xmax >= ${w} AND bbox_ymin <= ${n} AND bbox_ymax >= ${s})`;
}

/**
 * What a page query reads FROM: the file, or with a clip, a CTE holding only the clipped rows.
 *
 * Offline, only the row groups inside the saved areas are on the device, so the clip must stop
 * DuckDB reading any other. Two shapes that look equivalent do not (measured against a range log):
 * - Inline, the optimizer folds the clip in with a search and a sort and reads every row group's
 *   columns. Materializing the clipped rows first keeps the pruning.
 * - One OR across several areas is pruned by their combined envelope, which takes in the groups
 *   between them. One branch per area, each skipping rows an earlier one already took, prunes each
 *   branch to its own area.
 */
export function scanOf(src: string, clip?: [number, number, number, number][]): { cte: string; from: string } {
  const file = `read_parquet('${src}')`;
  if (!clip?.length) return { cte: "", from: file };
  const branches = clip.map((a, i) => `SELECT * FROM ${file} WHERE ${clipClause(a)}`
    + clip.slice(0, i).map((b) => ` AND NOT ${clipClause(b)}`).join(""));
  return { cte: `WITH clipped AS MATERIALIZED (${branches.join(" UNION ALL ")}) `, from: "clipped" };
}

const num = (v: unknown) => (v == null ? NaN : Number(v));

/** Each row group's extent and byte span in the file, from the footer alone. */
export type GroupSpan = { xmin: number; ymin: number; xmax: number; ymax: number; start: number; end: number };

export async function rowGroupSpans(parquetUrl: string): Promise<GroupSpan[]> {
  const db = await getDB();
  const conn = await db.connect();
  let borrowed: string | undefined;
  try {
    const src = borrowed = await registerUrl(parquetUrl);
    // A column chunk starts at its dictionary page when it has one, else at its first data page.
    const start = "CASE WHEN dictionary_page_offset > 0 AND dictionary_page_offset < data_page_offset"
      + " THEN dictionary_page_offset ELSE data_page_offset END";
    return (await conn.query(
      `SELECT min(${start})::BIGINT AS s, max(${start} + total_compressed_size)::BIGINT AS e,
              min(CASE WHEN path_in_schema = 'bbox_xmin' THEN CAST(stats_min AS DOUBLE) END) AS xmin,
              max(CASE WHEN path_in_schema = 'bbox_xmax' THEN CAST(stats_max AS DOUBLE) END) AS xmax,
              min(CASE WHEN path_in_schema = 'bbox_ymin' THEN CAST(stats_min AS DOUBLE) END) AS ymin,
              max(CASE WHEN path_in_schema = 'bbox_ymax' THEN CAST(stats_max AS DOUBLE) END) AS ymax
       FROM parquet_metadata('${src}') GROUP BY row_group_id ORDER BY row_group_id;`,
    )).toArray().map((r) => ({
      start: Number(r.s), end: Number(r.e),
      // NaN, not Number(null) = 0, when the file has no bbox columns: 0 would read as an extent.
      xmin: num(r.xmin), xmax: num(r.xmax), ymin: num(r.ymin), ymax: num(r.ymax),
    }));
  } finally {
    if (borrowed !== undefined) release(borrowed);
    await conn.close();
  }
}
// Inner ORDER BY expression (no leading " ORDER BY "): the user's sort (if any) then feature_id
// as a stable tiebreaker, so two rows with an equal sort key always page in the same order, and
// ordinalByFeatureId (a row_number() window over this same order) lines up with LIMIT/OFFSET paging
// exactly. `hasId` is false for pre-reingest parquet with no feature_id.
export function orderExpr(columns: string[], opts: PageOpts, hasId: boolean): string {
  // No explicit user sort means physical/file order (empty ORDER BY). This turns the page query from a
  // full-table TOP_N scan of every row into a streaming LIMIT read, which is what OOMs DuckDB-WASM on
  // large layers. ordinalByFeatureId numbers over an empty window in this same case, so the map-click
  // jump stays aligned with OFFSET paging whatever the physical row order is (e.g. after a spatial
  // re-sort), not only when feature_id happens to match it.
  if (!(opts.orderBy && columns.includes(opts.orderBy))) return "";
  const parts = [`${ident(opts.orderBy)} ${opts.desc ? "DESC" : "ASC"} NULLS LAST`];
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
  const hidden = new Set([...geomCols, ...(hasBbox ? BBOX_COLS : []), COVERING_COL, ID_COL]);
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
    const rowHidden = new Set([...geomCols, ...(hasBbox ? BBOX_COLS : []), COVERING_COL]);
    const colHidden = new Set([...rowHidden, ID_COL]);
    const columns = allCols.filter((c) => !colHidden.has(c));
    const types = typesOf(descRows);

    // WHERE = global free-text (OR across all columns) AND each per-column filter.
    const where = buildWhere(columns, opts);
    const scan = scanOf(src, opts.clip);
    const totalRes = await conn.query(`${scan.cte}SELECT count(*) AS n FROM ${scan.from}${where};`);
    const total = Number(totalRes.toArray()[0]?.n ?? 0);

    const order = buildOrder(columns, opts, hasId);
    // Select displayed cols + bbox cols explicitly (excluding geometry) so bbox survives for zoom.
    const sel = geomCols.length ? `* EXCLUDE (${geomCols.map(ident).join(", ")})` : "*";
    const res = await conn.query(
      `${scan.cte}SELECT ${sel} FROM ${scan.from}${where}${order} LIMIT ${opts.limit} OFFSET ${opts.offset};`,
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

/** 0-based position of the row carrying `featureId` under the SAME sort+filter the explorer shows,
 *  so the caller can jump the table to page `floor(pos / PAGE_SIZE)` and highlight row
 *  `pos % PAGE_SIZE`. Returns null if the parquet has no feature_id, or the row is filtered out of
 *  the current view. Numbers rows in the SAME order the page query uses (its ORDER BY when the user
 *  sorted, else scan order), so the position aligns with OFFSET paging exactly. */
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
    const colHidden = new Set([...GEOM_NAMES, ...(hasBbox ? BBOX_COLS : []), COVERING_COL, ID_COL]);
    const columns = allCols.filter((c) => !colHidden.has(c));
    const full: PageOpts = { ...opts, limit: 1, offset: 0 };
    const where = buildWhere(columns, full);
    // The window MUST order rows exactly like the page query, or the computed position won't line up
    // with OFFSET paging. With no user sort the page has no ORDER BY (physical scan order), so number
    // over an empty window (also scan order) instead of feature_id: that keeps the two in lockstep
    // even after the parquet is spatially re-sorted, when feature_id no longer equals row order.
    const ord = orderExpr(columns, full, true);
    const over = ord ? `ORDER BY ${ord}` : "";
    const scan = scanOf(src, opts.clip);
    const res = await conn.query(
      `${scan.cte}SELECT pos FROM (
         SELECT ${ident(ID_COL)} AS fid, row_number() OVER (${over}) - 1 AS pos
         FROM ${scan.from}${where}
       ) WHERE fid = ${Number(featureId)};`,
    );
    const pos = res.toArray()[0]?.pos;
    return pos == null ? null : Number(pos);
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

// DuckDB and GDAL calls are not interruptible, so cancelling suppresses the delivery instead.
// The caller takes a ticket BEFORE the pre-flight — that is the slow part, and a cancel during it
// has to count. Tickets are per-run, not a shared counter: cancelling one export must not
// silently swallow another's file. `current` lets a panel that remounted mid-export still show
// and cancel the run.
export async function exportItem(
  parquetUrl: string,
  stem: string,
  fmt: ExportFormat,
  clip?: [number, number, number, number], // [w,s,e,n] in 4326 — clip to this AOI
  epsg = 4326,                              // output CRS for the gdal formats (shp/gpkg/gdb/fgb)
  epoch = beginExport(),                    // from beginExport(), taken before the pre-flight ran
): Promise<void> {
  // Cancelled while the pre-flight was still running; end it so no ticket outlives the run.
  if (consumeIfCancelled(epoch)) { endRun(epoch); return; }
  const srs = safeEpsg(epsg);
  const db = await getDB();
  const conn = await db.connect();
  const id = ++seq;
  // Table names are database-scoped in DuckDB, so two runs would collide on a bare `raw`.
  const raw = `raw_${id}`, clipped = `clipped_${id}`;
  startRun({ id: epoch, stem, fmt });   // idempotent: the caller may have registered it already
  const deliver = (parts: Uint8Array[], filename: string, mime: string) => {
    if (!isCancelled(epoch)) triggerDownload(parts, filename, mime);
  };
  let csvOut: string | undefined;
  let seqOut: string | undefined;
  let borrowed: string | undefined;
  try {
    // Shared handle — see exportWarnings.
    const src = borrowed = await registerUrl(parquetUrl);
    // The clip goes INTO the read, against the per-row bbox columns the transform writes. Their
    // row-group stats prune whole groups before any geometry is decoded, so a small AOI over a
    // large topic reads a fraction of the file instead of all of it and then filtering.
    const cols0 = (await conn.query(`DESCRIBE SELECT * FROM read_parquet('${src}');`)).toArray()
      .map((r) => String(r.column_name));
    const prune = clip && BBOX_COLS.every((c) => cols0.includes(c))
      ? ` WHERE bbox_xmin <= ${clip[2]} AND bbox_xmax >= ${clip[0]}`
        + ` AND bbox_ymin <= ${clip[3]} AND bbox_ymax >= ${clip[1]}`
      : "";
    // Read FIRST, before loading spatial: spatial's GeoParquet reader trips over the
    // CRS metadata ("stoi: no conversion"). Plain read already yields a GEOMETRY column.
    await conn.query(`CREATE TABLE ${raw} AS SELECT * FROM read_parquet('${src}')${prune};`);
    await conn.query("INSTALL spatial; LOAD spatial;");
    const desc = await conn.query(`DESCRIBE ${raw};`);
    const descRows = desc.toArray();
    // Not always `geom` — the pre-flight already accepts `geometry` / `wkb_geometry`.
    const geomCol = geomColumn(descRows.map((r) => String(r.column_name)));
    const geomType = String(descRows.find((r) => String(r.column_name) === geomCol)?.column_type ?? "").toUpperCase();
    const geom = geomType.includes("BLOB") ? `ST_GeomFromWKB(${ident(geomCol)})` : ident(geomCol);

    // Exact intersect over the pruned set — the bbox prune above is a superset (bbox overlap is
    // not geometry overlap). Features are kept whole, not cut: "download what's in this area".
    let t = raw;
    if (clip) {
      const [w, s, e, n] = clip;
      await conn.query(
        `CREATE TABLE ${clipped} AS SELECT * FROM ${raw} WHERE ST_Intersects(${geom}, ST_MakeEnvelope(${w}, ${s}, ${e}, ${n}));`,
      );
      t = clipped;
    }

    if (fmt === "csv") {
      csvOut = `o${id}.csv`;
      // WKT geometry in the chosen output CRS (source is always 4326); attributes unchanged.
      // always_xy: geom is stored lon/lat, but EPSG:4326's authority axis order is lat/lon — without
      // this the transform reads longitude as latitude and returns inf.
      const wkt = srs === 4326 ? `ST_AsText(${geom})`
        : `ST_AsText(ST_Transform(${geom}, 'EPSG:4326', 'EPSG:${srs}', always_xy := true))`;
      const drop = cols0.includes(COVERING_COL) ? ` EXCLUDE (${ident(COVERING_COL)})` : "";
      await conn.query(`COPY (SELECT *${drop} REPLACE (${wkt} AS ${ident(geomCol)}) FROM ${t}) TO '${csvOut}' (HEADER, DELIMITER ',');`);
      deliver([await db.copyFileToBuffer(csvOut)], `${stem}.csv`, "text/csv");
      return;
    }

    // One Feature per line, written straight to a DuckDB file — the features never exist as a
    // JS value or string, so peak memory is one buffer instead of Arrow rows + string + MEMFS copy.
    const cols = descRows.map((r) => String(r.column_name)).filter((c) => !GEOM_NAMES.includes(c) && c !== COVERING_COL);
    seqOut = `o${id}.geojsonl`;
    await conn.query(`COPY (${featureSeqSql(cols, geom, t)}) TO '${seqOut}' (FORMAT JSON, ARRAY false);`);
    const seqBytes = await db.copyFileToBuffer(seqOut);
    // Free the DuckDB side now; in `finally` it would outlive the GDAL conversion.
    await conn.query(`DROP TABLE IF EXISTS ${raw}; DROP TABLE IF EXISTS ${clipped};`).catch(() => {});
    await db.dropFile(seqOut).catch(() => {});
    seqOut = undefined;

    if (fmt === "geojson") {
      deliver(wrapFeatureSeqInPlace(seqBytes), `${stem}.geojson`, "application/geo+json");
      return;
    }

    // gpkg / shp / gdb / fgb via gdal3.js (~40 MB, lazy-loaded here only).
    const { convertFeatureSeq, GDAL_TARGETS } = await import("./gdal");
    const { bytes, filename, mime } = await convertFeatureSeq(seqBytes, stem, GDAL_TARGETS[fmt], srs);
    deliver([bytes], filename, mime);
  } finally {
    await conn.query(`DROP TABLE IF EXISTS ${raw}; DROP TABLE IF EXISTS ${clipped};`).catch(() => {});
    await conn.close();
    if (borrowed !== undefined) release(borrowed);
    if (csvOut) await db.dropFile(csvOut).catch(() => {});
    if (seqOut) await db.dropFile(seqOut).catch(() => {});
    endRun(epoch);
  }
}

// ---- Shapefile pre-flight ----
// The Esri Shapefile format silently mangles data past its limits; check before exporting so the
// user isn't handed a broken file. Range reads make this cheap at any size: the checks come off
// the footer (column stats, row count, GeoParquet metadata), and the one scan that reads column
// data is budgeted (see MEASURE_TEXT_BUDGET).
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
  widthsEstimated: boolean;            // text widths averaged rather than measured (see the budget)
  estReadBytes: number;                // bytes the export fetches, after row-group pruning
  rowGroups: number;                   // how finely the file is grouped — what pruning can work with
  minClipBytes: number;                // the least any clip could read: the largest single group
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

// Text has no fixed width, so the .dbf estimate needs the longest decoded value. That is a real
// column read, and the whole point of range reads is not to pull more than a query needs — so
// above this much text (footer bytes, scaled by any clip) fall back to the average instead.
const MEASURE_TEXT_BUDGET = 64 * 1024 ** 2;

// A read this big takes long enough that the user should choose to wait rather than discover it.
// Shared with the panel, which reports the figure alongside the warning.
export const SLOW_READ_BYTES = 256 * 1024 ** 2;

/** Pre-flight an export. The size limits apply to every format: all of them read the rows the
 *  export covers into the tab, and the four GDAL ones then run them through the same wasm
 *  instance.
 *  The shapefile-only findings (field names, field count, single geometry type, the 2 GB per-file
 *  cap) are filled in for `shp` alone. */
export async function exportWarnings(
  parquetUrl: string,
  fmt: ExportFormat,
  clip?: [number, number, number, number],
): Promise<ShapefileWarnings> {
  const shp = fmt === "shp";
  const db = await getDB();
  const conn = await db.connect();
  let borrowed: string | undefined;
  try {
    // Shared handle: a private registration would re-fetch the footer and chunks this URL has
    // already read for the explorer or a previous pre-flight.
    const src = borrowed = await registerUrl(parquetUrl);
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

    // Per-row-group extent and transfer size, from the footer. This is what a clip can prune.
    const groups: RowGroup[] = (await conn.query(
      `SELECT sum(total_compressed_size)::BIGINT AS bytes,
              min(CASE WHEN path_in_schema = 'bbox_xmin' THEN CAST(stats_min AS DOUBLE) END) AS xmin,
              max(CASE WHEN path_in_schema = 'bbox_xmax' THEN CAST(stats_max AS DOUBLE) END) AS xmax,
              min(CASE WHEN path_in_schema = 'bbox_ymin' THEN CAST(stats_min AS DOUBLE) END) AS ymin,
              max(CASE WHEN path_in_schema = 'bbox_ymax' THEN CAST(stats_max AS DOUBLE) END) AS ymax
       FROM parquet_metadata('${src}') GROUP BY row_group_id;`,
    )).toArray().map((r) => ({
      bytes: Number(r.bytes),
      xmin: Number(r.xmin), xmax: Number(r.xmax), ymin: Number(r.ymin), ymax: Number(r.ymax),
    }));
    // From the schema, not from the aggregates: a missing bbox column aggregates to SQL NULL and
    // Number(null) is 0, which would read as a real extent at (0, 0) and prune everything away.
    const usable = BBOX_COLS.every((c) => colTypes.has(c));
    const estReadBytes = estimateReadBytes(groups, usable ? clip : undefined);
    // A row group is the smallest unit a reader can skip, so the biggest one is the floor on what
    // any clip can get the download down to.
    const minClipBytes = usable ? Math.max(0, ...groups.map((g) => g.bytes)) : estReadBytes;
    const fullRows = Number((await conn.query(
      `SELECT sum(num_rows)::BIGINT AS n FROM parquet_file_metadata('${src}');`,
    )).toArray()[0].n);

    // A clip only scales the estimate. The per-row bbox columns answer that without decoding
    // geometry (and prune row groups); fall back to a real intersect if the file lacks them.
    let where = "";
    if (clip) {
      const [w, sy, e, n] = clip;
      if (usable) {
        where = ` WHERE bbox_xmin <= ${e} AND bbox_xmax >= ${w} AND bbox_ymin <= ${n} AND bbox_ymax >= ${sy}`;
      } else {
        await conn.query("INSTALL spatial; LOAD spatial;");
        where = ` WHERE ST_Intersects(${geomExpr}, ST_MakeEnvelope(${w}, ${sy}, ${e}, ${n}))`;
      }
    }
    const rowCount = where
      ? Number((await conn.query(`SELECT count(*) AS n FROM ${from}${where};`)).toArray()[0].n)
      : fullRows;

    // Shapefile only: this costs a full geometry decode under a clip, where the file-level
    // GeoParquet `geo` key no longer describes the selection.
    let names: string[] | null = null;
    if (holdsOneGeomType(fmt) && !where) {
      const kv = (await conn.query(
        `SELECT decode(value) AS v FROM parquet_kv_metadata('${src}') WHERE decode(key) = 'geo';`,
      )).toArray();
      if (kv.length) names = geoMetadataTypes(String(kv[0].v), geom);
    }
    if (holdsOneGeomType(fmt) && !names) {
      await conn.query("INSTALL spatial; LOAD spatial;");
      names = (await conn.query(
        `SELECT DISTINCT ST_GeometryType(${geomExpr}) AS g FROM ${from}${where}
         ${where ? "AND" : "WHERE"} ${geomExpr} IS NOT NULL;`,
      )).toArray().map((r) => String(r.g));
    }
    const baseTypes = baseGeometryTypes(names ?? []);

    // Decoded text length, which the footer cannot give (see dbfFieldWidth). This is a real read:
    // it fetches the text chunks of every row group the clip admits, which is what the budget
    // above bounds.
    const measured = cols.filter((c) => needsMeasuredWidth(colTypes.get(c) ?? "VARCHAR"));
    // Scaled by the bytes the read will fetch, not by a row fraction: the scan pulls whole column
    // chunks for every row group the clip admits, which is what estReadBytes already measures.
    const allGroupBytes = groups.reduce((n, g) => n + g.bytes, 0);
    const textBytes = measured.reduce((n, c) => n + (colBytes.get(c) ?? 0), 0)
      * (allGroupBytes ? estReadBytes / allGroupBytes : 1);
    const widthsEstimated = textBytes > MEASURE_TEXT_BUDGET;
    const lengths = new Map<string, { max: number; avg: number }>();
    if (measured.length && !widthsEstimated) {
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
      if (m) return { name: c, type, maxBytes: m.max, avgBytes: m.avg };
      // Unmeasured: the footer's bytes-per-row is the best width available. dbfFieldWidth(type, 0)
      // would be 1 for text, which under-reports a wide table by enough to hide the 2 GB cap.
      const avg = needsMeasuredWidth(type) && fullRows ? (colBytes.get(c) ?? 0) / fullRows
        : dbfFieldWidth(type, 0);
      return { name: c, type, maxBytes: avg, avgBytes: avg };
    });
    const geomBytes = Math.round((colBytes.get(geom) ?? 0) * scale);
    const { estShpBytes, estDbfBytes, over2gb } = estimateShapefileBytes(fields, rowCount, geomBytes);
    const geojsonBytes = estimateGeoJSONBytes(fields, rowCount, geomBytes);
    // A single-file format writes roughly the shapefile pair without the dbf padding.
    const outputBytes = shp ? estShpBytes + estDbfBytes : geomBytes + rowCount * 40;
    const estPeakBytes = estimateExportPeakBytes(fmt, geojsonBytes, outputBytes);
    const overBrowserLimit = estPeakBytes > WASM_HEAP_BUDGET;

    const tooManyFields = shp && cols.length > 255;
    const mixedGeometry = baseTypes.length > 1 ? baseTypes : [];
    return {
      longNames, collisions, fieldCount: cols.length, tooManyFields,
      mixedGeometry, rowCount, estShpBytes, estDbfBytes, over2gb: shp && over2gb,
      estPeakBytes, overBrowserLimit, widthsEstimated,
      estReadBytes, rowGroups: groups.length, minClipBytes,
      any: longNames.length > 0 || collisions.length > 0 || tooManyFields
        || mixedGeometry.length > 0 || (shp && over2gb) || overBrowserLimit
        || estReadBytes > SLOW_READ_BYTES || widthsEstimated,
    };
  } finally {
    if (borrowed !== undefined) release(borrowed);
    await conn.close();
  }
}
