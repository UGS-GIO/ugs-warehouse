// "What's under my click", across every layer, drawn or not.
//
// Left-click identifies features on the layers already on the map (MapLibre renders them). This
// answers for all of them: for each vector layer it reads the one tile containing the point from
// the published PMTiles archive, decodes it, and hit-tests the features, so a layer is listed only
// when something of it is actually there, not merely because its extent covers the point.
import { VectorTile } from "@mapbox/vector-tile";
import Pbf from "pbf";
import { MercatorCoordinate } from "maplibre-gl";
import { PMTiles } from "pmtiles";

export type Hit = { layer: string; properties: Record<string, string | number | boolean> };

// Features this close to the point count as under it, in screen pixels: a finger or cursor is
// not a mathematical point, and a fault line is a pixel wide.
const TOLERANCE_PX = 6;
// Zoom to read at: the archive's own detail, capped where a single tile is still small.
const MAX_READ_ZOOM = 14;

const archives = new Map<string, PMTiles>();
const archiveFor = (url: string) => {
  let a = archives.get(url);
  if (!a) archives.set(url, (a = new PMTiles(url)));
  return a;
};

// ---- geometry, in tile coordinates ----

type P = { x: number; y: number };

/** Even-odd point-in-polygon over every ring, so holes subtract without classifying rings. */
export function inRings(p: P, rings: P[][]): boolean {
  let inside = false;
  for (const ring of rings) {
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const a = ring[i], b = ring[j];
      if ((a.y > p.y) !== (b.y > p.y) && p.x < ((b.x - a.x) * (p.y - a.y)) / (b.y - a.y) + a.x) inside = !inside;
    }
  }
  return inside;
}

/** Distance from a point to the segment a-b. */
export function toSegment(p: P, a: P, b: P): number {
  const dx = b.x - a.x, dy = b.y - a.y;
  const len = dx * dx + dy * dy;
  const t = len ? Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / len)) : 0;
  return Math.hypot(p.x - (a.x + t * dx), p.y - (a.y + t * dy));
}

/** Whether a feature of MVT `type` (1 point, 2 line, 3 polygon) is at `p`, within `tol`. */
export function hits(type: number, geometry: P[][], p: P, tol: number): boolean {
  if (type === 3) return inRings(p, geometry);
  if (type === 2) return geometry.some((line) => line.some((a, i) => i > 0 && toSegment(p, line[i - 1], a) <= tol));
  if (type === 1) return geometry.some((pts) => pts.some((q) => Math.hypot(q.x - p.x, q.y - p.y) <= tol));
  return false;
}

// ---- reading a layer ----

/**
 * The features of one PMTiles layer at a point. `mapZoom` is the map's current zoom, which sets
 * how many tile units the pixel tolerance spans.
 */
export async function identifyAt(url: string, lon: number, lat: number, mapZoom: number,
  source?: PMTiles): Promise<Hit[]> {
  const p = source ?? archiveFor(url);
  const h = await p.getHeader();
  if (h.tileType !== 1) return [];                        // raster archive: nothing to hit
  const z = Math.min(h.maxZoom, MAX_READ_ZOOM);
  if (z < h.minZoom) return [];
  const n = 2 ** z;
  const at = MercatorCoordinate.fromLngLat({ lng: lon, lat });
  const fx = at.x * n, fy = at.y * n;
  const x = Math.floor(fx), y = Math.floor(fy);
  const tile = await p.getZxy(z, x, y);
  if (!tile) return [];

  const vt = new VectorTile(new Pbf(new Uint8Array(tile.data)));
  const out: Hit[] = [];
  for (const name of Object.keys(vt.layers)) {
    const layer = vt.layers[name];
    const point = { x: (fx - x) * layer.extent, y: (fy - y) * layer.extent };
    // MapLibre draws a vector tile 512 px wide at its own zoom, so at map zoom Z one tile spans
    // 512 * 2^(Z - z) px; the pixel tolerance, converted to tile units.
    const tol = (TOLERANCE_PX * layer.extent) / (512 * 2 ** (mapZoom - z));
    for (let i = 0; i < layer.length; i++) {
      const f = layer.feature(i);
      if (hits(f.type, f.loadGeometry(), point, tol)) out.push({ layer: name, properties: f.properties });
    }
  }
  return out;
}

// Keys and values that identify a row rather than describe it: a label made of them reads as noise
// ("groundshaking_current.fid-23e49f4e_1a07e84e73d_-1143").
const ID_KEY = /(^|_)(fid|id|objectid|globalid|guid|uuid|ogc_fid|shape_\w+|ugs_key)$/i;
const ID_VALUE = /fid-|^[0-9a-f]{8}-[0-9a-f]{4}-|_[0-9a-f]{10,}/i;

/** A short label for a hit: the first name-like attribute, else the first descriptive text. */
export function hitLabel(props: Hit["properties"]): string | null {
  const keys = Object.keys(props).filter((k) => !ID_KEY.test(k) && !ID_VALUE.test(String(props[k])));
  const named = keys.find((k) => /^(name|title|label|unit.?name|unitname|type|class|category)$/i.test(k));
  const key = named ?? keys.find((k) => typeof props[k] === "string" && String(props[k]).length < 80);
  return key ? String(props[key]) : null;
}
