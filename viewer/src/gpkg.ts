// Build a minimal OGC GeoPackage (a SQLite file) entirely in the browser via sql.js.
// GDAL's GPKG driver can't init sqlite in WASM, but sql.js is sqlite-wasm and works —
// so we hand-write the required gpkg_* metadata tables + the features table, encoding
// each geometry as a GeoPackageBinary blob (header + WKB). ArcGIS Pro reads this.
import initSqlJs from "sql.js";

const SQLJS_CDN = "https://cdn.jsdelivr.net/npm/sql.js@1.14.1/dist";
const WGS84_WKT =
  'GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563]],' +
  'PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]]';

export type GpkgColumn = { name: string; sqlType: string };
export type GpkgRow = { wkb: Uint8Array; props: unknown[] };

/** Map a DuckDB column type to a SQLite/GPKG column type. */
export function sqliteType(duckType: string): string {
  const t = duckType.toUpperCase();
  if (t.includes("BOOL") || /\b(TINY|SMALL|BIG|HUGE)?INT\b/.test(t) || t.includes("INTEGER")) return "INTEGER";
  if (t.includes("DOUBLE") || t.includes("FLOAT") || t.includes("REAL") || t.includes("DECIMAL") || t.includes("NUMERIC")) return "REAL";
  return "TEXT";
}

/** GeoPackageBinary: 'GP' magic + version + flags(LE, no envelope) + int32 srs_id + WKB. */
function geoPackageBinary(wkb: Uint8Array, srsId = 4326): Uint8Array {
  const out = new Uint8Array(8 + wkb.length);
  out[0] = 0x47; // G
  out[1] = 0x50; // P
  out[2] = 0x00; // version 0
  out[3] = 0x01; // flags: little-endian, envelope=none
  new DataView(out.buffer).setInt32(4, srsId, true);
  out.set(wkb, 8);
  return out;
}

export async function buildGpkg(opts: {
  table: string;
  columns: GpkgColumn[];
  bbox: [number, number, number, number];
  rows: GpkgRow[];
}): Promise<Uint8Array> {
  const { table, columns, bbox, rows } = opts;
  const SQL = await initSqlJs({ locateFile: (f) => `${SQLJS_CDN}/${f}` });
  const db = new SQL.Database();
  const q = (name: string) => `"${name.replace(/"/g, '""')}"`;

  try {
    // GPKG identity (application_id 'GPKG' = 0x47504B47, version 1.2 = 10200).
    db.run("PRAGMA application_id = 1196444487;");
    db.run("PRAGMA user_version = 10200;");

    db.run(`
      CREATE TABLE gpkg_spatial_ref_sys (
        srs_name TEXT NOT NULL, srs_id INTEGER PRIMARY KEY,
        organization TEXT NOT NULL, organization_coordsys_id INTEGER NOT NULL,
        definition TEXT NOT NULL, description TEXT
      );
      CREATE TABLE gpkg_contents (
        table_name TEXT PRIMARY KEY, data_type TEXT NOT NULL, identifier TEXT UNIQUE,
        description TEXT DEFAULT '', last_change TEXT NOT NULL,
        min_x DOUBLE, min_y DOUBLE, max_x DOUBLE, max_y DOUBLE, srs_id INTEGER
      );
      CREATE TABLE gpkg_geometry_columns (
        table_name TEXT NOT NULL, column_name TEXT NOT NULL, geometry_type_name TEXT NOT NULL,
        srs_id INTEGER NOT NULL, z TINYINT NOT NULL, m TINYINT NOT NULL,
        PRIMARY KEY (table_name, column_name)
      );
    `);

    const srs = db.prepare(
      "INSERT INTO gpkg_spatial_ref_sys VALUES (?,?,?,?,?,?)",
    );
    srs.run(["Undefined cartesian SRS", -1, "NONE", -1, "undefined", null]);
    srs.run(["Undefined geographic SRS", 0, "NONE", 0, "undefined", null]);
    srs.run(["WGS 84", 4326, "EPSG", 4326, WGS84_WKT, null]);
    srs.free();

    const colDDL = columns.map((c) => `${q(c.name)} ${c.sqlType}`).join(", ");
    db.run(`CREATE TABLE ${q(table)} (fid INTEGER PRIMARY KEY AUTOINCREMENT, geom BLOB${colDDL ? ", " + colDDL : ""});`);

    db.run(
      `INSERT INTO gpkg_contents (table_name, data_type, identifier, last_change, min_x, min_y, max_x, max_y, srs_id)
       VALUES (?, 'features', ?, '2000-01-01T00:00:00.000Z', ?, ?, ?, ?, 4326);`,
      [table, table, bbox[0], bbox[1], bbox[2], bbox[3]],
    );
    db.run(
      `INSERT INTO gpkg_geometry_columns VALUES (?, 'geom', 'GEOMETRY', 4326, 0, 0);`,
      [table],
    );

    const colNames = columns.map((c) => q(c.name)).join(", ");
    const placeholders = columns.map(() => "?").join(", ");
    const ins = db.prepare(
      `INSERT INTO ${q(table)} (geom${colNames ? ", " + colNames : ""}) VALUES (?${placeholders ? ", " + placeholders : ""});`,
    );
    for (const r of rows) {
      ins.run([geoPackageBinary(r.wkb), ...(r.props as (string | number | Uint8Array | null)[])]);
    }
    ins.free();

    return db.export();
  } finally {
    db.close();
  }
}
