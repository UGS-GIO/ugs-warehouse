// Pure, unit-tested map logic shared by the item-detail map(s). Kept framework-free so the refactor
// to a single persistent map has a verified core (the freeze itself only reproduces on a real GPU,
// so these tests guard the parts we CAN check deterministically).
// Type-only import — value imports from stac would pull in its module-level `location` read, which
// isn't available in the (node) test env. The tests here stay framework/DOM-free.
import type { StacDoc } from "@/stac";

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

// Where a released drag settles. A flick (speed in sheet-heights per second, + is up) goes one
// detent past where the sheet is, in the flick's direction, however short the drag was.
export const FLICK_SPEED = 0.8;
export function releaseDetent(frac: number, speed: number): number {
  if (speed > FLICK_SPEED) return Math.min(DETENTS.filter((d) => d <= frac + 1e-9).length, DETENTS.length - 1);
  if (speed < -FLICK_SPEED) return Math.max(DETENTS.filter((d) => d < frac - 1e-9).length - 1, 0);
  return nearestDetent(frac);
}

// Resizable pane size, clamped. Non-finite (a stored value from an older build, or NaN off a
// pointer event) falls back to the default rather than collapsing the pane to zero.
export function clampSize(n: number, min: number, max: number, fallback: number): number {
  if (!Number.isFinite(n)) return fallback;
  return Math.max(min, Math.min(max, n));
}

// Table row-click / map-feature-click → map highlight + fly target. `key` identifies the SELECTION
// so the map re-flies on every distinct pick, even two features at the same lat/lon (identical
// bbox). `featureId` is the feature to outline via setFeatureState on the PMTiles tile (the exact
// geometry is already on the map, so nothing is read from the parquet). `bbox` drives the fly.
export type FocusSel = { bbox?: [number, number, number, number]; featureId?: number; key?: string | number };

// A topic toggled on in the map. Built by App from the active set × allItems. One of: a vector
// layer (PMTiles → pmHref/pmLayer), a raster COG (cogHref), or a raster PMTiles mosaic
// (rasterPmHref — the per-scale geologic-map mosaics, served via the pmtiles:// protocol).
export type ActiveLayer = {
  id: string; title: string; bbox?: number[];
  pmHref?: string; pmLayer?: string; styleUrl?: string;
  cogHref?: string;
  rasterPmHref?: string;
  // Zarr datacube — one object, because the store is useless without the variable and the dims to
  // pin. Resolved from STAC once, so the layer list and the map agree on what is drawn.
  zarr?: { href: string; variable: string; pinDims: string[] };
};

// Geometry gates for the unstyled fallback render — without them its circle layer puts a dot on
// every polygon and line VERTEX. Multi- names are for GeoJSON sources; tiles use the singular.
type GeomFilter = ["match", ["geometry-type"], string[], true, false];
export const GEOM_FILTER: Record<"fill" | "line" | "point", GeomFilter> = {
  fill: ["match", ["geometry-type"], ["Polygon", "MultiPolygon"], true, false],
  line: ["match", ["geometry-type"], ["LineString", "MultiLineString", "Polygon", "MultiPolygon"], true, false],
  point: ["match", ["geometry-type"], ["Point", "MultiPoint"], true, false],
};

// A catalog item's footprint for the Coverage overlay — its bbox (drawn as a rectangle) + enough
// to open it on click. Aspatial items (no bbox) are filtered out by the caller.
export type Footprint = { href: string; id: string; title: string; bbox: number[] };

// Distinct, saturated, mid-dark colors that read on the light, dark, and satellite basemaps. Cycled
// per active layer by colorForId. Kept larger than a handful of layers to hold down repeats.
export const LAYER_COLORS = [
  "#d1491c", "#2b6cdf", "#1a7f4b", "#9333ea", "#d97706", "#0891b2",
  "#be185d", "#65a30d", "#ca8a04", "#4338ca", "#a21caf", "#0f766e",
];

// A layer's swatch color, keyed to its id — NOT its position in the active set — so toggling a layer
// or dragging it up/down the draw order never recolors it or its neighbours (that stability is the
// point; an index-based color would reshuffle on every reorder). A deterministic string hash picks a
// palette slot. Colors are stable but NOT guaranteed unique: two ids can land on the same slot, more
// often the more layers are on at once, so the legend labels each layer to disambiguate. A fully
// collision-free scheme would trade off either the curated palette (generated hues) or that per-id
// stability (assigning distinct colors across the active set, which recolors on add/remove).
export const colorForId = (id: string): string => {
  let h = 0;
  for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) | 0;
  return LAYER_COLORS[Math.abs(h) % LAYER_COLORS.length];
};

// The `l` search param. Three states, and collapsing two of them is why the last layer could not
// be turned off: absent = no choice yet (the open item draws), `none` = every layer off, else a
// list. The router drops an empty param, so "off" needs a value of its own.
export const NO_LAYERS = "none";

export const parseLayerParam = (l?: string): string[] | undefined =>
  l === NO_LAYERS ? [] : l ? l.split(",").filter(Boolean) : undefined;

export const layerParam = (ids?: string[]): string | undefined =>
  ids ? (ids.length ? ids.join(",") : NO_LAYERS) : undefined;

// Move the active layer at `from` to `to` (drag-reorder the draw order), the rest shifting to fill.
// Returns a NEW array and never mutates the input; an out-of-range index leaves the order untouched,
// so a stray drag event can't corrupt the ?l= set.
export const reorderLayers = (ids: string[], from: number, to: number): string[] => {
  if (from < 0 || to < 0 || from >= ids.length || to >= ids.length) return ids.slice();
  const next = ids.slice();
  const [moved] = next.splice(from, 1);
  next.splice(to, 0, moved);
  return next;
};

// Map/source/layer ids are keyed by a STABLE slug of the layer id, never the array index: index-based
// ids shift when a layer toggles off and maplibre throws "source id changed" on the rename. Shared by
// the map render and the reorder reconcile so their ids can't drift.
export const slugOf = (id: string): string => id.replace(/[^a-zA-Z0-9_]/g, "_");

// The GL layer ids each active layer draws, in bottom→top draw order, matching what map.tsx renders:
// a raster PMTiles mosaic → one `rpm-<slug>-raster`; a COG → one `cog-<slug>-raster` (only once its
// protocol is ready); a vector layer → its resolved style layers `pm-<slug>-0..N` (styledCount), else
// the unstyled fallback `pm-<slug>-fill/line/circle`; a zarr datacube → none (a deck.gl overlay draws
// it, outside maplibre's layer stack). Flattened across `layers` in order, so the reorder reconcile
// can walk the list and moveLayer each. Pure so it's unit-tested against the render.
export function orderedSublayerIds(
  layers: ActiveLayer[],
  opts: { styledCount: (id: string) => number | undefined; cogReady: boolean },
): string[] {
  return layers.flatMap((l) => {
    const s = slugOf(l.id);
    if (l.zarr) return [];
    if (l.rasterPmHref) return [`rpm-${s}-raster`];
    if (l.cogHref) return opts.cogReady ? [`cog-${s}-raster`] : [];
    const n = opts.styledCount(l.id);
    // map.tsx renders `styleLayers ? styleLayers.map(...) : fallback` — a resolved-but-empty style
    // ([]) is truthy there and draws nothing, so mirror it with `n != null` (0 → no ids), not `n > 0`.
    return n != null
      ? Array.from({ length: n }, (_, li) => `pm-${s}-${li}`)
      : [`pm-${s}-fill`, `pm-${s}-line`, `pm-${s}-circle`];
  });
}
