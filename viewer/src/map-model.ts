// Pure, unit-tested map logic shared by the item-detail map(s). Kept framework-free so the refactor
// to a single persistent map has a verified core (the freeze itself only reproduces on a real GPU,
// so these tests guard the parts we CAN check deterministically).
// Type-only import — value imports from stac would pull in its module-level `location` read, which
// isn't available in the (node) test env. The tests here stay framework/DOM-free.
import type { StacDoc } from "./stac";

// Which map surface an item needs. A single consolidated map renders the right sources per kind, so
// switching between items of different kinds swaps sources instead of remounting a whole component
// (and its WebGL context). "footprint" = no tiles, just draw the item's outline; "none" = aspatial.
// Caller supplies the presence flags (computed with stac's pmtilesLink/rasterTilesAsset/cogAsset) so
// this stays a pure, testable priority function.
export type MapKind = "vector" | "raster" | "cog" | "footprint" | "none";
export type MapKindFlags = { vector?: boolean; raster?: boolean; cog?: boolean; footprint?: boolean };

export function mapKindOf(f: MapKindFlags): MapKind {
  if (f.vector) return "vector";
  if (f.raster) return "raster";
  if (f.cog) return "cog";
  if (f.footprint) return "footprint";
  return "none";
}

// The footprint flag for mapKindOf: an item is footprint-drawable if it has geometry or a real bbox.
export function hasFootprint(item: StacDoc | undefined): boolean {
  return Boolean(item && (item.geometry || (Array.isArray(item.bbox) && item.bbox.length >= 4)));
}

// A [w,s,e,n] lon/lat bbox, or undefined if the item's bbox is missing/malformed. Handles the STAC
// 2D (4 = [w,s,e,n]) and 3D (6 = [w,s,minZ,e,n,maxZ]) forms, and REJECTS out-of-range values — some
// catalog items carry a bad bbox (projected metres, or a longitude leaked into the latitude slot).
// maplibre throws "Invalid LngLat" on those in fitBounds, which with no error boundary takes the map
// down; here a bad bbox just means "no auto-fit" (the map keeps its default view).
export function validBbox(bb: number[] | undefined): [number, number, number, number] | undefined {
  if (!Array.isArray(bb)) return undefined;
  const h = bb.length >= 6 ? [bb[0], bb[1], bb[3], bb[4]]
    : bb.length >= 4 ? [bb[0], bb[1], bb[2], bb[3]] : undefined;
  if (!h) return undefined;
  const [w, s, e, n] = h;
  const okLon = (v: number) => Number.isFinite(v) && v >= -180 && v <= 180;
  const okLat = (v: number) => Number.isFinite(v) && v >= -90 && v <= 90;
  return okLon(w) && okLon(e) && okLat(s) && okLat(n) ? [w, s, e, n] : undefined;
}

// Same bbox as maplibre LngLatBounds corners [[sw],[ne]], or undefined.
export function boundsOf(item: StacDoc | undefined): [[number, number], [number, number]] | undefined {
  const b = validBbox(item?.bbox);
  return b ? [[b[0], b[1]], [b[2], b[3]]] : undefined;
}

// Map feature-click → table selection. The nonce bumps on every click so re-clicking the SAME
// feature id still re-fires the downstream table effect (a bare id wouldn't change, so it wouldn't).
export type MapPick = { id: number; nonce: number };
export function nextPick(prev: MapPick | null, id: number): MapPick {
  return { id, nonce: (prev?.nonce ?? 0) + 1 };
}

// Mobile sheet snap points, as a fraction of the map area: peek / half / full.
export const DETENTS = [0.06, 0.55, 0.92] as const;

// Which detent a drag ended nearest. Ties go to the lower one — releasing mid-way biases toward
// showing more map, which is the thing the sheet is covering. The epsilon is what makes that true:
// an exact midpoint is rarely exact in binary floating point, so a hair either way would otherwise
// decide it (0.305 between 0.06 and 0.55 lands 2e-17 nearer the upper one).
export function nearestDetent(frac: number): number {
  let best = 0;
  for (let i = 1; i < DETENTS.length; i++) {
    if (Math.abs(DETENTS[i] - frac) < Math.abs(DETENTS[best] - frac) - 1e-9) best = i;
  }
  return best;
}

// Resizable pane size, clamped. Non-finite (a stored value from an older build, or NaN off a
// pointer event) falls back to the default rather than collapsing the pane to zero.
export function clampSize(n: number, min: number, max: number, fallback: number): number {
  if (!Number.isFinite(n)) return fallback;
  return Math.max(min, Math.min(max, n));
}

// Table row-click → map fly target. `key` identifies the SELECTION (row offset / feature id) so the
// map re-flies on every distinct pick — even two features at the same lat/lon (identical bbox). The
// bbox→geometry upgrade within one pick reuses the same key, so it doesn't double-fly.
export type FocusSel = { bbox?: [number, number, number, number]; geometry?: GeoJSON.Geometry | null; key?: string | number };

// A topic toggled on in the map. Built by App from the active set × allItems. One of: a vector
// layer (PMTiles → pmHref/pmLayer), a raster COG (cogHref), or a raster PMTiles mosaic
// (rasterPmHref — the per-scale geologic-map mosaics, served via the pmtiles:// protocol).
export type ActiveLayer = {
  id: string; title: string; bbox?: number[];
  pmHref?: string; pmLayer?: string; styleUrl?: string;
  cogHref?: string;
  rasterPmHref?: string;
  // Zarr datacube: the store plus what to slice out of it. Carried here (not re-read from STAC in
  // the map) so the layer list and the map agree on which variable is drawn.
  zarrHref?: string; zarrVariable?: string; zarrPinDims?: string[];
};

// A catalog item's footprint for the Coverage overlay — its bbox (drawn as a rectangle) + enough
// to open it on click. Aspatial items (no bbox) are filtered out by the caller.
export type Footprint = { href: string; id: string; title: string; bbox: number[] };

// Distinct colors cycled per active layer.
export const LAYER_COLORS = ["#d1491c", "#2b6cdf", "#1a7f4b", "#9333ea", "#d97706", "#0891b2", "#be185d", "#65a30d"];
export const colorFor = (i: number) => LAYER_COLORS[i % LAYER_COLORS.length];

// The `l` search param. Three states, and collapsing two of them is why the last layer could not
// be turned off: absent = no choice yet (the open item draws), `none` = every layer off, else a
// list. The router drops an empty param, so "off" needs a value of its own.
export const NO_LAYERS = "none";

export const parseLayerParam = (l?: string): string[] | undefined =>
  l === NO_LAYERS ? [] : l ? l.split(",").filter(Boolean) : undefined;

export const layerParam = (ids?: string[]): string | undefined =>
  ids ? (ids.length ? ids.join(",") : NO_LAYERS) : undefined;
