// Client-side export: read an item's GeoParquet over the CDN in DuckDB-WASM and
// hand it back as SHP / GeoJSON / CSV — no server, no perms. DuckDB (+ spatial) and
// the shapefile writer are imported lazily the first time someone exports, so they
// never weigh down the initial bundle.
//
// Why not DuckDB's GDAL `COPY`: every GDAL output driver (GPKG/GeoJSON/Shapefile)
// fails in WASM — GPKG's sqlite VFS can't init ("file is not a database") and the
// GeoJSON/Shapefile drivers error on write ("Cannot write feature"). So we only use
// DuckDB's NATIVE writers (CSV) + build GeoJSON/SHP in JS from `ST_AsGeoJSON`.
export type ExportFormat = "shp" | "gpkg" | "geojson" | "csv";

export const FORMATS: { id: ExportFormat; label: string }[] = [
  { id: "shp", label: "Shapefile (zip)" },
  { id: "gpkg", label: "GeoPackage" },
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
  const blob = new Blob([copy], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

// Arrow rows carry BigInt (int64 cols) + Dates; make them JSON/shapefile-safe.
function sanitize(v: unknown): unknown {
  if (typeof v === "bigint") return Number(v);
  if (v instanceof Date) return v.toISOString();
  return v;
}

let seq = 0;

export async function exportItem(parquetUrl: string, stem: string, fmt: ExportFormat): Promise<void> {
  const duckdb = await import("@duckdb/duckdb-wasm");
  const db = await getDB();
  const conn = await db.connect();
  // Unique src name per export: the DuckDB instance is a singleton, so its virtual FS
  // and registered files persist between exports.
  const id = ++seq;
  const src = `s${id}.parquet`;
  const wrote: string[] = [];
  try {
    await db.registerFileURL(src, parquetUrl, duckdb.DuckDBDataProtocol.HTTP, false);
    // Read FIRST, before loading spatial: spatial's GeoParquet reader trips over the
    // CRS metadata ("stoi: no conversion"). Plain read already yields a GEOMETRY column.
    await conn.query(`CREATE TABLE raw AS SELECT * FROM read_parquet('${src}');`);
    await conn.query("INSTALL spatial; LOAD spatial;");

    // Most sources give GEOMETRY directly; only hydrate if geom landed as raw WKB BLOB.
    const desc = await conn.query(`DESCRIBE raw;`);
    const geomType = String(desc.toArray().find((r) => String(r.column_name) === GEOM)?.column_type ?? "").toUpperCase();
    const geom = geomType.includes("BLOB") ? `ST_GeomFromWKB(${GEOM})` : GEOM;

    if (fmt === "csv") {
      const out = `o${id}.csv`;
      wrote.push(out);
      await conn.query(`COPY (SELECT * REPLACE (ST_AsText(${geom}) AS ${GEOM}) FROM raw) TO '${out}' (HEADER, DELIMITER ',');`);
      triggerDownload(await db.copyFileToBuffer(out), `${stem}.csv`, "text/csv");
      return;
    }

    if (fmt === "gpkg") {
      const { buildGpkg, sqliteType } = await import("./gpkg");
      const columns = desc
        .toArray()
        .filter((r) => String(r.column_name) !== GEOM)
        .map((r) => ({ name: String(r.column_name), sqlType: sqliteType(String(r.column_type)) }));
      const ext = (await conn.query(
        `SELECT ST_XMin(e) a, ST_YMin(e) b, ST_XMax(e) c, ST_YMax(e) d FROM (SELECT ST_Extent(${geom}) e FROM raw);`,
      )).toArray()[0];
      const bbox: [number, number, number, number] = [Number(ext?.a ?? 0), Number(ext?.b ?? 0), Number(ext?.c ?? 0), Number(ext?.d ?? 0)];
      const res = await conn.query(`SELECT ST_AsWKB(${geom}) AS __wkb, * EXCLUDE (${GEOM}) FROM raw;`);
      const rows = res.toArray().map((row) => {
        const o = row.toJSON() as Record<string, unknown>;
        const wkb = o.__wkb as Uint8Array;
        delete o.__wkb;
        return { wkb, props: columns.map((c) => sanitize(o[c.name]) as string | number | null) };
      });
      const bytes = await buildGpkg({ table: stem, columns, bbox, rows });
      triggerDownload(bytes, `${stem}.gpkg`, "application/geopackage+sqlite3");
      return;
    }

    // geojson + shp: build a FeatureCollection in JS from ST_AsGeoJSON (no GDAL).
    const res = await conn.query(`SELECT * EXCLUDE (${GEOM}), ST_AsGeoJSON(${geom}) AS __g FROM raw;`);
    const fc: GeoJSON.FeatureCollection = {
      type: "FeatureCollection",
      features: res.toArray().map((row): GeoJSON.Feature => {
        const o = row.toJSON() as Record<string, unknown>;
        const g = o.__g as string | null;
        delete o.__g;
        const properties: Record<string, unknown> = {};
        for (const [k, v] of Object.entries(o)) properties[k] = sanitize(v);
        return { type: "Feature", geometry: g ? JSON.parse(g) : null, properties };
      }),
    };

    if (fmt === "geojson") {
      triggerDownload(new TextEncoder().encode(JSON.stringify(fc)), `${stem}.geojson`, "application/geo+json");
      return;
    }

    // shp → pure-JS shapefile writer, returns a zip (shp/shx/dbf/prj, split by geom type).
    const shpwrite = await import("@mapbox/shp-write");
    const buf = (await shpwrite.zip(fc, { outputType: "arraybuffer", compression: "DEFLATE" })) as ArrayBuffer;
    triggerDownload(new Uint8Array(buf), `${stem}.zip`, "application/zip");
  } finally {
    await conn.query("DROP TABLE IF EXISTS raw;").catch(() => {});
    await conn.close();
    for (const f of [src, ...wrote]) await db.dropFile(f).catch(() => {});
  }
}
