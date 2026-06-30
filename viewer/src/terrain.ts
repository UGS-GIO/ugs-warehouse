// Build a 3D terrain mesh for the fence-diagram OrbitView (deck.gl SimpleMeshLayer), live from
// USGS 3DEP. The 3DEP ImageServer sends CORS (access-control-allow-origin: *), so the browser samples
// elevation directly — no hosted tiles, no baked files, works for any pub's bbox automatically. 3DEP
// over Utah IS the state UGRC lidar (1 m where it exists) — the same surface the geologists used, which
// is why the outcropping fence tops register on the terrain to within a few metres.
//
// Public domain (The National Map), no key. Use is interactive/per-view only — modest concurrency, not
// bulk extraction (USGS provides staged downloads for that).

// deck.gl SimpleMeshLayer mesh (attributes form). POSITION in local metres (z = elevation, NOT yet
// exaggerated — getScale applies that); TEXCOORD_0 into the map-sheet image.
export type TerrainMesh = {
  attributes: {
    POSITION: { value: Float32Array; size: 3 };
    TEXCOORD_0: { value: Float32Array; size: 2 };
  };
  indices: { value: Uint32Array; size: 1 };
};

// Optional precomputed elevation grid (e.g. a hosted high-res override). Kept for future needs; the
// default path is live 3DEP. Row-major north→south then west→east.
export type Heightfield = { bbox: [number, number, number, number]; nx: number; ny: number; z: number[] };

const merX = (lon: number) => (lon + 180) / 360; // 0..1 web-mercator (for COG texture registration)
const merY = (lat: number) => {
  const s = Math.sin((lat * Math.PI) / 180);
  return 0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI);
};

const SAMPLES_URL = "https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/getSamples";

// Sample elevations for a batch of [lon,lat] points via 3DEP getSamples. Form-urlencoded POST is a
// CORS "simple request" (no preflight). Returns metres per input point (0 on miss/error).
async function sample3DEP(points: number[][]): Promise<number[]> {
  const out = new Array(points.length).fill(0);
  const body = new URLSearchParams({
    geometry: JSON.stringify({ points, spatialReference: { wkid: 4326 } }),
    geometryType: "esriGeometryMultipoint",
    returnFirstValueOnly: "true",
    f: "json",
    sampleCount: String(points.length),
  });
  try {
    const r = await fetch(SAMPLES_URL, {
      method: "POST",
      headers: { "content-type": "application/x-www-form-urlencoded" },
      body,
    });
    const j = await r.json();
    for (const s of j.samples ?? []) {
      if (typeof s.locationId === "number" && s.value != null) out[s.locationId] = parseFloat(s.value);
    }
  } catch { /* leave zeros for this batch */ }
  return out;
}

/**
 * Live terrain mesh from 3DEP over the dataset bbox.
 * @param center [lon,lat] local-frame origin (must match the fence's centre)
 * @param scale  [mPerDegLon, mPerDegLat] local-frame scale (must match the fence)
 * @param grid   grid resolution per side (more = sharper relief + more requests)
 */
export async function buildMeshFrom3DEP(
  bbox: [number, number, number, number],
  center: [number, number],
  scale: [number, number],
  grid = 64,
): Promise<TerrainMesh | null> {
  const pts: number[][] = [];
  for (let j = 0; j < grid; j++) {
    const lat = bbox[3] + (bbox[1] - bbox[3]) * (j / (grid - 1)); // north → south
    for (let i = 0; i < grid; i++) {
      pts.push([bbox[0] + (bbox[2] - bbox[0]) * (i / (grid - 1)), lat]);
    }
  }
  // Batch (getSamples caps per request) + bounded concurrency (courtesy to the public service).
  const B = 500;
  const batches: number[][][] = [];
  for (let k = 0; k < pts.length; k += B) batches.push(pts.slice(k, k + B));
  const results: number[][] = new Array(batches.length);
  let next = 0;
  const worker = async () => { while (next < batches.length) { const m = next++; results[m] = await sample3DEP(batches[m]); } };
  await Promise.all(Array.from({ length: 10 }, worker));
  const z = results.flat();
  if (z.every((v) => !v)) return null; // no coverage / all failed

  return meshFromGrid(bbox, grid, grid, z, center, scale);
}

// Build a TerrainMesh from a precomputed elevation grid (hosted override path).
export function meshFromHeightfield(hf: Heightfield, center: [number, number], scale: [number, number]): TerrainMesh {
  return meshFromGrid(hf.bbox, hf.nx, hf.ny, hf.z, center, scale);
}

// Elevation grid (row-major N→S, W→E) → deck mesh. POSITION in the fence's local metre frame; z stays
// absolute elevation (getScale exaggerates). TEXCOORD_0 in mercator fraction so the (mercator) COG
// sheet registers with the surface.
function meshFromGrid(
  bbox: [number, number, number, number], nx: number, ny: number, z: number[],
  center: [number, number], scale: [number, number],
): TerrainMesh {
  const [cLon, cLat] = center, [mLon, mLat] = scale;
  const positions = new Float32Array(nx * ny * 3);
  const texCoords = new Float32Array(nx * ny * 2);
  const mxMin = merX(bbox[0]), mxMax = merX(bbox[2]);
  const myTop = merY(bbox[3]), myBot = merY(bbox[1]);
  for (let j = 0; j < ny; j++) {
    const lat = bbox[3] + (bbox[1] - bbox[3]) * (j / (ny - 1));
    for (let i = 0; i < nx; i++) {
      const lon = bbox[0] + (bbox[2] - bbox[0]) * (i / (nx - 1));
      const k = j * nx + i;
      positions[k * 3] = (lon - cLon) * mLon;
      positions[k * 3 + 1] = (lat - cLat) * mLat;
      positions[k * 3 + 2] = z[k];
      texCoords[k * 2] = (merX(lon) - mxMin) / (mxMax - mxMin || 1);
      texCoords[k * 2 + 1] = (merY(lat) - myTop) / (myBot - myTop || 1);
    }
  }
  const indices = new Uint32Array((nx - 1) * (ny - 1) * 6);
  let o = 0;
  for (let j = 0; j < ny - 1; j++) {
    for (let i = 0; i < nx - 1; i++) {
      const a = j * nx + i, b = a + 1, c = a + nx, d = c + 1;
      indices[o++] = a; indices[o++] = c; indices[o++] = b;
      indices[o++] = b; indices[o++] = c; indices[o++] = d;
    }
  }
  return {
    attributes: { POSITION: { value: positions, size: 3 }, TEXCOORD_0: { value: texCoords, size: 2 } },
    indices: { value: indices, size: 1 },
  };
}
