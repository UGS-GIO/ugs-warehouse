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
