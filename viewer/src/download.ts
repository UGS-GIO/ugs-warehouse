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

// The transform writes the geometry column as `geom` (GEOMETRY 4326); pub/external parquet may
// use `geometry` / `wkb_geometry`. GEOM = the canonical name (export); GEOM_NAMES = all hidden
// from the explorer table + probed for the row-geometry fetch.
const GEOM = "geom";
const GEOM_NAMES = ["geom", "geometry", "wkb_geometry"];

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

// Stable per-row id the transform stamps (1..N, hilbert order) into BOTH the GeoParquet and the
// PMTiles (as the MVT feature id). It's the join key for map↔table linking. Hidden from the
// displayed columns (synthetic noise) but kept on each row object so the table can highlight a
// row the map picked, and used as a deterministic ORDER BY tiebreaker so OFFSET paging and the
// feature_id→ordinal lookup agree exactly.
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
// Inner ORDER BY expression (no leading " ORDER BY "): the user's sort (if any) then feature_id
// as a stable tiebreaker, so two rows with an equal sort key always page in the same order — and
// the feature_id→ordinal lookup (a row_number() window over this same expression) lines up with
// LIMIT/OFFSET paging exactly. `hasId` is false for pre-reingest parquet with no feature_id.
function orderExpr(columns: string[], opts: PageOpts, hasId: boolean): string {
  const parts: string[] = [];
  if (opts.orderBy && columns.includes(opts.orderBy))
    parts.push(`${ident(opts.orderBy)} ${opts.desc ? "DESC" : "ASC"} NULLS LAST`);
  if (hasId) parts.push(`${ident(ID_COL)} ASC`);
  return parts.join(", ");
}
const buildOrder = (columns: string[], opts: PageOpts, hasId: boolean): string => {
  const e = orderExpr(columns, opts, hasId);
  return e ? ` ORDER BY ${e}` : "";
};

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
    const hasId = allCols.includes(ID_COL);
    const geomCols = GEOM_NAMES.filter((c) => allCols.includes(c));
    // Row object drops geometry + bbox (noise) but KEEPS feature_id so the table can highlight a
    // map-picked row. Displayed columns additionally drop feature_id (synthetic, not user data).
    const rowHidden = new Set([...geomCols, ...(hasBbox ? BBOX_COLS : [])]);
    const colHidden = new Set([...rowHidden, ID_COL]);
    const columns = allCols.filter((c) => !colHidden.has(c));
    const types: Record<string, ColType> = {};
    for (const r of descRows) {
      const name = String(r.column_name);
      if (!colHidden.has(name)) types[name] = colType(String(r.column_type ?? ""));
    }

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
  try {
    const src = await registerUrl(parquetUrl);
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
    const res = await conn.query(
      `SELECT pos FROM (
         SELECT ${ident(ID_COL)} AS fid, row_number() OVER (ORDER BY ${ord}) - 1 AS pos
         FROM ${from}${where}
       ) WHERE fid = ${Number(featureId)};`,
    );
    const pos = res.toArray()[0]?.pos;
    return pos == null ? null : Number(pos);
  } finally {
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
  try {
    const src = await registerUrl(parquetUrl);
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
    await conn.close();
  }
}

/** Read every row of a (small) 3D GeoParquet as {props, geometry} with **Z retained** — for the 3D
 *  fence viewer, which needs the whole file (not paged) and the elevation ordinate. No spatial
 *  extension (geometry read as WKB BLOB, parsed in JS). props = all non-geometry columns. */
export async function readFeatures3D(
  parquetUrl: string,
): Promise<Array<{ props: Record<string, unknown>; geometry: GeoJSON.Geometry | null }>> {
  const db = await getDB();
  const conn = await db.connect();
  try {
    const src = await registerUrl(parquetUrl);
    const from = `read_parquet('${src}')`;
    const desc = await conn.query(`DESCRIBE SELECT * FROM ${from};`);
    const allCols = desc.toArray().map((r) => String(r.column_name));
    const geomCol = GEOM_NAMES.find((c) => allCols.includes(c));
    const propCols = allCols.filter((c) => c !== geomCol);
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

// ---- Shapefile pre-flight ----
// The Esri Shapefile format silently mangles data past its limits; check before exporting so the user
// isn't handed a broken file. Deterministic checks (field names/count/geometry) are read from the data;
// size is an estimate (parquet is compressed ~4× vs the uncompressed .shp/.dbf).
export interface ShapefileWarnings {
  longNames: string[];                 // > 10 chars → truncated by the driver
  collisions: [string, string][];      // fields that collapse to the same 10-char name → data loss
  fieldCount: number;                  // shapefile hard cap is 255
  tooManyFields: boolean;
  mixedGeometry: string[];             // >1 base geometry type → shapefile can't hold them together
  rowCount: number;
  estBytes: number;                    // rough uncompressed-size estimate
  over2gb: boolean;
  any: boolean;                        // true if anything worth warning about
}

export async function shapefileWarnings(
  parquetUrl: string,
  clip?: [number, number, number, number],
): Promise<ShapefileWarnings> {
  const duckdb = await import("@duckdb/duckdb-wasm");
  const db = await getDB();
  const conn = await db.connect();
  const id = ++seq;
  const src = `chk${id}.parquet`;
  try {
    await db.registerFileURL(src, parquetUrl, duckdb.DuckDBDataProtocol.HTTP, false);
    await conn.query(`CREATE TABLE chk AS SELECT * FROM read_parquet('${src}');`);
    const desc = (await conn.query("DESCRIBE chk;")).toArray();
    const cols = desc.map((r) => String(r.column_name)).filter((c) => !GEOM_NAMES.includes(c));

    // Field-name limits (10 chars) + post-truncation collisions.
    const longNames = cols.filter((c) => c.length > 10);
    const seen = new Map<string, string>();
    const collisions: [string, string][] = [];
    for (const c of cols) {
      const t = c.slice(0, 10).toLowerCase();
      if (seen.has(t)) collisions.push([seen.get(t)!, c]);
      else seen.set(t, c);
    }

    const fullRows = Number((await conn.query("SELECT count(*) n FROM chk;")).toArray()[0].n);
    let rowCount = fullRows;

    // Geometry: distinct BASE types (strip MULTI / Z / M). >1 base = can't share a shapefile.
    await conn.query("INSTALL spatial; LOAD spatial;");
    const gd = (await conn.query("DESCRIBE chk;")).toArray();
    const geomType = String(gd.find((r) => String(r.column_name) === GEOM)?.column_type ?? "").toUpperCase();
    const geomExpr = geomType.includes("BLOB") ? `ST_GeomFromWKB(${GEOM})` : GEOM;
    let where = "";
    if (clip) {
      const [w, s, e, n] = clip;
      where = ` WHERE ST_Intersects(${geomExpr}, ST_MakeEnvelope(${w}, ${s}, ${e}, ${n}))`;
      rowCount = Number((await conn.query(`SELECT count(*) n FROM chk${where};`)).toArray()[0].n);
    }
    const gtypes = (await conn.query(
      `SELECT DISTINCT ST_GeometryType(${geomExpr}) g FROM chk${where} WHERE ${geomExpr} IS NOT NULL;`,
    )).toArray().map((r) => String(r.g).toUpperCase());
    const baseTypes = [...new Set(gtypes.map((g) => g.replace(/^ST_/, "").replace(/^MULTI/, "").replace(/[ZM]+$/, "")))];

    // Size estimate: parquet Content-Length × ~4 (shp/.dbf are uncompressed), scaled by the clip ratio.
    let estBytes = 0;
    try {
      const head = await fetch(parquetUrl, { method: "HEAD" });
      const pq = Number(head.headers.get("content-length")) || 0;
      if (pq && fullRows) estBytes = Math.round(pq * 4 * (rowCount / fullRows));
    } catch { /* HEAD blocked → skip the size estimate */ }
    const over2gb = estBytes > 2 * 1024 ** 3;

    const tooManyFields = cols.length > 255;
    const mixedGeometry = baseTypes.length > 1 ? baseTypes : [];
    return {
      longNames, collisions, fieldCount: cols.length, tooManyFields,
      mixedGeometry, rowCount, estBytes, over2gb,
      any: longNames.length > 0 || collisions.length > 0 || tooManyFields || mixedGeometry.length > 0 || over2gb,
    };
  } finally {
    await conn.query("DROP TABLE IF EXISTS chk;").catch(() => {});
    await conn.close();
    await db.dropFile(src).catch(() => {});
  }
}
