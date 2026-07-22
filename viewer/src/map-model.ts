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

// Table row-click → map fly target. `key` identifies the SELECTION (row offset / feature id) so the
// map re-flies on every distinct pick — even two features at the same lat/lon (identical bbox). The
// bbox→geometry upgrade within one pick reuses the same key, so it doesn't double-fly.
export type FocusSel = { bbox?: [number, number, number, number]; geometry?: GeoJSON.Geometry | null; key?: string | number };
