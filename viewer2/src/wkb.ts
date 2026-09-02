// Minimal WKB → GeoJSON geometry parser. The warehouse writes GeoParquet with standard ISO WKB
// (EPSG:4326), so the explorer can read a single row's geometry as a BLOB — no spatial extension
// load (which trips DuckDB-WASM's GeoParquet CRS reader and would pollute the shared instance).
// Handles Point / LineString / Polygon + their Multi* and GeometryCollection. By default Z/M are
// read past and dropped (2D); pass keepZ to retain the Z ordinate (3D fence diagrams need it).

type Pos = number[]; // [x,y] or [x,y,z]

class Reader {
  private dv: DataView;
  private off = 0;
  constructor(buf: ArrayBuffer, readonly keepZ: boolean) { this.dv = new DataView(buf); }
  private le = true;
  u8() { const v = this.dv.getUint8(this.off); this.off += 1; return v; }
  u32() { const v = this.dv.getUint32(this.off, this.le); this.off += 4; return v; }
  f64() { const v = this.dv.getFloat64(this.off, this.le); this.off += 8; return v; }
  order() { this.le = this.u8() === 1; }
}

// WKB type code carries dimensionality: ISO adds 1000 (Z) / 2000 (M) / 3000 (ZM); the EWKB high
// bits (0x80000000 Z, 0x40000000 M) are also tolerated. Returns [baseType 1..7, extraOrdinates].
function decodeType(raw: number): [number, number] {
  let extra = 0;
  if (raw & 0x80000000) extra++;            // EWKB Z
  if (raw & 0x40000000) extra++;            // EWKB M
  const iso = (raw & 0x0fffffff) % 1000;    // strip ISO 1000/2000/3000 band
  const band = Math.floor((raw & 0x0fffffff) / 1000);
  if (band === 1 || band === 2) extra = Math.max(extra, 1);
  if (band === 3) extra = Math.max(extra, 2);
  return [iso, extra];
}

function readPoint(r: Reader, extra: number): Pos {
  const x = r.f64(); const y = r.f64();
  let z: number | undefined;
  for (let i = 0; i < extra; i++) { const v = r.f64(); if (i === 0 && r.keepZ) z = v; } // keep first extra (Z)
  return z === undefined ? [x, y] : [x, y, z];
}
const readRing = (r: Reader, extra: number): Pos[] => {
  const n = r.u32(); const ring: Pos[] = [];
  for (let i = 0; i < n; i++) ring.push(readPoint(r, extra));
  return ring;
};

function readGeom(r: Reader): GeoJSON.Geometry {
  r.order();
  const [type, extra] = decodeType(r.u32());
  switch (type) {
    case 1: return { type: "Point", coordinates: readPoint(r, extra) };
    case 2: return { type: "LineString", coordinates: readRing(r, extra) };
    case 3: {
      const n = r.u32(); const rings: Pos[][] = [];
      for (let i = 0; i < n; i++) rings.push(readRing(r, extra));
      return { type: "Polygon", coordinates: rings };
    }
    case 4: {
      const n = r.u32(); const pts: Pos[] = [];
      for (let i = 0; i < n; i++) pts.push((readGeom(r) as GeoJSON.Point).coordinates as Pos);
      return { type: "MultiPoint", coordinates: pts };
    }
    case 5: {
      const n = r.u32(); const lines: Pos[][] = [];
      for (let i = 0; i < n; i++) lines.push((readGeom(r) as GeoJSON.LineString).coordinates as Pos[]);
      return { type: "MultiLineString", coordinates: lines };
    }
    case 6: {
      const n = r.u32(); const polys: Pos[][][] = [];
      for (let i = 0; i < n; i++) polys.push((readGeom(r) as GeoJSON.Polygon).coordinates as Pos[][]);
      return { type: "MultiPolygon", coordinates: polys };
    }
    case 7: {
      const n = r.u32(); const geometries: GeoJSON.Geometry[] = [];
      for (let i = 0; i < n; i++) geometries.push(readGeom(r));
      return { type: "GeometryCollection", geometries };
    }
    default: throw new Error(`unsupported WKB type ${type}`);
  }
}

/** Parse standard WKB bytes to a GeoJSON geometry. `keepZ` retains the Z ordinate (default 2D).
 *  Returns null on empty/garbage input. */
export function wkbToGeoJSON(bytes: Uint8Array, keepZ = false): GeoJSON.Geometry | null {
  if (!bytes || bytes.byteLength < 5) return null;
  // Copy to a tight ArrayBuffer (DuckDB buffers may be SharedArrayBuffer-backed / offset).
  const buf = bytes.slice().buffer;
  try {
    return readGeom(new Reader(buf, keepZ));
  } catch {
    return null;
  }
}
