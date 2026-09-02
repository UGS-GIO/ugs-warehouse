// Pure, unit-tested core for the Discover view: facet extraction + result filtering + sorting over
// the loaded catalog items. Framework/DOM-free — type-only imports of the item + STAC shapes, plus
// the already-pure validBbox from map-model — so it runs in the (node) test env. The view component
// wires these to MiniSearch (the shared search-index) and the live map; neither belongs in this layer.
import type { ItemRef } from "./browse";
import { categorize, collectionLabel, docIdOf, formatsOf, hasGeometry, propsOf,
  title, typeOf } from "./item-view";
import { validBbox } from "./map-model";

// The field getters now live in item-view.ts (one source of truth, shared with browse.tsx). Re-export
// the discovery-facing names so this module's API and the view stay unchanged.
export { collectionLabel, docIdOf, hasGeometry, typeOf };
export const discoveryTitle = title;

export type FacetCount = { key: string; label: string; n: number };
export type Facets = {
  collections: FacetCount[]; categories: FacetCount[]; types: FacetCount[];
  formats: FacetCount[]; geometry: FacetCount[];
};

// Geometry facet keys (also the `geometry` field of a FacetSelection).
export const GEOM_HAS = "has";
export const GEOM_NONE = "none";

const ranked = (m: Map<string, FacetCount>): FacetCount[] =>
  [...m.values()].sort((a, b) => b.n - a.n || a.key.localeCompare(b.key));

const bump = (m: Map<string, FacetCount>, key: string, label: string) => {
  const cur = m.get(key);
  if (cur) cur.n++;
  else m.set(key, { key, label, n: 1 });
};

// Counts for each facet group over the full loaded set (stable — counts don't shift as filters
// toggle, so the rail reads like a table of contents rather than jumping around).
export function extractFacets(items: ItemRef[]): Facets {
  const colls = new Map<string, FacetCount>();
  const cats = new Map<string, FacetCount>();
  const types = new Map<string, FacetCount>();
  const formats = new Map<string, FacetCount>();
  let has = 0;
  let none = 0;
  for (const it of items) {
    if (!it.data) continue; // not yet loaded → not a facetable result
    if (it.collId) bump(colls, it.collId, collectionLabel(it.collId));
    const cat = categorize(it);
    bump(cats, cat.key, cat.label);
    const t = typeOf(it);
    if (t) bump(types, t, t);
    for (const f of formatsOf(it)) bump(formats, f, f);
    if (hasGeometry(it)) has++;
    else none++;
  }
  const geometry: FacetCount[] = [];
  if (has) geometry.push({ key: GEOM_HAS, label: "On the map", n: has });
  if (none) geometry.push({ key: GEOM_NONE, label: "No footprint", n: none });
  return {
    collections: ranked(colls), categories: ranked(cats), types: ranked(types),
    formats: ranked(formats), geometry,
  };
}

export type FacetSelection = {
  collections: string[];
  types: string[];
  categories?: string[]; // one home category per item (categorize)
  formats?: string[];    // an item matches if it carries ANY selected format
  geometry: "all" | typeof GEOM_HAS | typeof GEOM_NONE;
};

// Keep items matching every active facet group — AND across the groups, OR within a multi-select
// group (collections / categories / types / formats). An item without loaded data is never a result.
export function applyFacets(items: ItemRef[], sel: FacetSelection): ItemRef[] {
  const colls = new Set(sel.collections);
  const cats = new Set(sel.categories ?? []);
  const types = new Set(sel.types);
  const formats = new Set(sel.formats ?? []);
  return items.filter((it) => {
    if (!it.data) return false;
    if (colls.size && !colls.has(it.collId)) return false;
    if (cats.size && !cats.has(categorize(it).key)) return false;
    if (types.size && !types.has(typeOf(it))) return false;
    if (formats.size && !formatsOf(it).some((f) => formats.has(f))) return false;
    if (sel.geometry === GEOM_HAS && !hasGeometry(it)) return false;
    if (sel.geometry === GEOM_NONE && hasGeometry(it)) return false;
    return true;
  });
}

// AABB overlap of two [w,s,e,n] boxes (each validated/normalized first). Touching edges count as
// overlap. A missing/invalid box on either side is "no overlap".
export function bboxIntersects(a: number[] | undefined, b: number[] | undefined): boolean {
  const va = validBbox(a);
  const vb = validBbox(b);
  if (!va || !vb) return false;
  return va[0] <= vb[2] && va[2] >= vb[0] && va[1] <= vb[3] && va[3] >= vb[1];
}

// "Search this area": keep items whose footprint meets the current map viewport. Aspatial items (no
// valid bbox) can't be in an area, so they drop out while the filter is active. A missing/invalid
// viewport is a no-op (everything passes).
export function filterByViewport(items: ItemRef[], viewport: number[] | undefined): ItemRef[] {
  if (!validBbox(viewport)) return items;
  return items.filter((it) => bboxIntersects(it.data?.bbox, viewport));
}

// ISO datetime (or empty) for date sorts — lexicographic on ISO strings == chronological.
const datetimeOf = (it: ItemRef): string => String(propsOf(it).datetime ?? "");

export type SortKey = "relevance" | "title" | "newest" | "oldest";
export const SORTS: { key: SortKey; label: string }[] = [
  { key: "relevance", label: "Best match" },
  { key: "newest", label: "Newest" },
  { key: "oldest", label: "Oldest" },
  { key: "title", label: "Title A–Z" },
];

// Order results for display. "relevance" preserves the caller's order (the MiniSearch score order,
// or the facet-count order when there's no query) — so it's the identity. The others return a NEW
// array (never mutate the input). Items missing a datetime sort last under both date orders, so an
// undated pub never jumps to the top of "Newest".
export function sortItems(items: ItemRef[], key: SortKey): ItemRef[] {
  if (key === "relevance") return items;
  const out = [...items];
  if (key === "title") return out.sort((a, b) => discoveryTitle(a).localeCompare(discoveryTitle(b)));
  const dir = key === "newest" ? -1 : 1;
  return out.sort((a, b) => {
    const da = datetimeOf(a);
    const db = datetimeOf(b);
    if (da === db) return 0;
    if (!da) return 1; // undated → last, regardless of direction
    if (!db) return -1;
    return dir * da.localeCompare(db);
  });
}

// ---- URL <-> Discover state (the boundary) ------------------------------------------------------
// The Discover view's whole filter/sort/layout state lives in the URL so a landing tile or a shared
// link reproduces the view. These two pure functions are the validated boundary: parse the raw search
// (all strings, possibly bad) into a typed state, and serialize a state back to a search patch that
// drops defaults (so a pristine view stays a clean `/discover`). Namespaced keys — q / collections
// / types / category / formats / geometry / sort / layout / density / area — never touch App's c/i/l/s.
export type Layout = "gallery" | "list";
export type Density = "comfortable" | "compact";
export type Area = [number, number, number, number];

export type DiscoveryState = {
  q: string;
  collections: string[];
  categories: string[];
  types: string[];
  formats: string[];
  geometry: FacetSelection["geometry"];
  sort: SortKey;
  layout: Layout;
  density: Density;
  area: Area | null;
};

export const DEFAULT_DISCOVERY: DiscoveryState = {
  q: "", collections: [], categories: [], types: [], formats: [],
  geometry: "all", sort: "relevance", layout: "gallery", density: "comfortable", area: null,
};

const str = (v: unknown): string => (typeof v === "string" ? v : "");
// A CSV param → a trimmed, de-duplicated, non-empty string list.
const toStringArray = (v: unknown): string[] =>
  [...new Set(str(v).split(",").map((s) => s.trim()).filter(Boolean))];
const oneOf = <T extends string>(v: unknown, allowed: readonly T[], fallback: T): T =>
  (allowed as readonly string[]).includes(str(v)) ? (str(v) as T) : fallback;

const GEOMETRIES = ["all", GEOM_HAS, GEOM_NONE] as const;
const LAYOUTS_K = ["gallery", "list"] as const;
const DENSITIES_K = ["comfortable", "compact"] as const;
const SORT_KEYS = SORTS.map((s) => s.key);

const parseArea = (v: unknown): Area | null => {
  const parts = str(v).split(",").map(Number);
  if (parts.length !== 4 || parts.some((n) => !Number.isFinite(n))) return null;
  return validBbox(parts) ? (parts as Area) : null;
};

/** Raw search params → a validated DiscoveryState (bad/absent values fall back to the default). */
export function parseDiscovery(sp: Record<string, unknown>): DiscoveryState {
  return {
    q: str(sp.q),
    collections: toStringArray(sp.collections),
    categories: toStringArray(sp.category),
    types: toStringArray(sp.types),
    formats: toStringArray(sp.formats),
    geometry: oneOf(sp.geometry, GEOMETRIES, "all"),
    sort: oneOf(sp.sort, SORT_KEYS, "relevance"),
    layout: oneOf(sp.layout, LAYOUTS_K, "gallery"),
    density: oneOf(sp.density, DENSITIES_K, "comfortable"),
    area: parseArea(sp.area),
  };
}

const csv = (list: string[]): string | undefined => (list.length ? list.join(",") : undefined);

/** A DiscoveryState → a search patch (each Discover key set or cleared). Defaults serialize to
 *  `undefined` so they drop out of the URL, keeping a pristine view a bare `/discover`. */
export function discoveryPatch(s: DiscoveryState): Record<string, string | undefined> {
  return {
    // Keep the RAW query (internal + trailing spaces intact) whenever it has real content — the box is
    // controlled from the URL and round-trips q on every keystroke, so trimming here would eat the
    // space the instant it's typed ("salt lake" → "saltlake"). Consumers trim before searching.
    q: s.q.trim() ? s.q : undefined,
    collections: csv(s.collections),
    category: csv(s.categories),
    types: csv(s.types),
    formats: csv(s.formats),
    geometry: s.geometry === "all" ? undefined : s.geometry,
    sort: s.sort === "relevance" ? undefined : s.sort,
    layout: s.layout === "gallery" ? undefined : s.layout,
    density: s.density === "comfortable" ? undefined : s.density,
    area: s.area ? s.area.join(",") : undefined,
  };
}

/** The active-filter chips (removable) for the row above the cards — pure so it's testable. Each
 *  carries the patch that REMOVES just that filter. Text search + sort/layout/density aren't "filters". */
export type FilterChip = { id: string; label: string; patch: Partial<DiscoveryState> };
export function activeChips(s: DiscoveryState, labelFor: {
  collection: (key: string) => string; category: (key: string) => string;
}): FilterChip[] {
  const chips: FilterChip[] = [];
  for (const key of s.collections)
    chips.push({ id: `coll:${key}`, label: labelFor.collection(key), patch: { collections: s.collections.filter((k) => k !== key) } });
  for (const key of s.categories)
    chips.push({ id: `cat:${key}`, label: labelFor.category(key), patch: { categories: s.categories.filter((k) => k !== key) } });
  for (const key of s.types)
    chips.push({ id: `type:${key}`, label: key, patch: { types: s.types.filter((k) => k !== key) } });
  for (const key of s.formats)
    chips.push({ id: `fmt:${key}`, label: key, patch: { formats: s.formats.filter((k) => k !== key) } });
  if (s.geometry === GEOM_HAS) chips.push({ id: "geom", label: "On the map", patch: { geometry: "all" } });
  if (s.geometry === GEOM_NONE) chips.push({ id: "geom", label: "No footprint", patch: { geometry: "all" } });
  if (s.area) chips.push({ id: "area", label: "Map area", patch: { area: null } });
  return chips;
}
