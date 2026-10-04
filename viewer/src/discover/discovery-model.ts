// Pure, unit-tested core for the Discover view: facet extraction + result filtering + sorting over
// the loaded catalog items. Framework/DOM-free — type-only imports of the item + STAC shapes, plus
// the already-pure validBbox from map-model and the query parser — so it runs in the (node) test env.
// The view component wires these to MiniSearch (the shared search-index) and the live map; neither
// belongs in this layer.
import type { ItemRef } from "@/catalog/browse";
import { CATEGORIES, categorize, collectionLabel, datetimeIsPublished, docIdOf, formatsOf, hasGeometry, propsOf,
  scaleDenominator, title, typeOf, year } from "@/catalog/item-view";
import { baseTerms, parseQuery, type Query } from "@/data/query";
import { validBbox } from "@/map/map-model";

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
export type Tile = { key: string; label: string; count: number };

// The category list Discover shows before a search: each item's one home category, in taxonomy order, with counts,
// keeping only the categories that have items.
export function categoryTiles(items: ItemRef[]): Tile[] {
  const counts = new Map<string, number>();
  for (const it of items) {
    const { key } = categorize(it);
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  return CATEGORIES
    .map((c) => ({ key: c.key, label: c.label, count: counts.get(c.key) ?? 0 }))
    .filter((t) => t.count > 0);
}

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

/** A facet's rows plus any selected value the current search left with no matches, at 0, so a
 *  filter that's on can always be seen and turned off where it was turned on. */
export function withSelected(facets: FacetCount[], selected: string[], label: (key: string) => string): FacetCount[] {
  const have = new Set(facets.map((f) => f.key));
  const missing = selected.filter((k) => !have.has(k)).map((k) => ({ key: k, label: label(k), n: 0 }));
  return missing.length ? [...missing, ...facets] : facets;
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

// ISO dates for date sorts — lexicographic on ISO strings == chronological. A publication's datetime
// is when it was published; a layer's is usually when the warehouse last loaded it, which says nothing
// about how new the data is. So date sorts rank by publication date, and the rest only order among
// themselves (datetimeIsPublished, until #479).
const publishedOf = (it: ItemRef): string =>
  (datetimeIsPublished(propsOf(it)) ? String(propsOf(it).datetime ?? "") : "");
const loadedOf = (it: ItemRef): string =>
  (datetimeIsPublished(propsOf(it)) ? "" : String(propsOf(it).datetime ?? ""));

// Two ISO dates in `dir` order, an empty one last whichever the direction.
const byDate = (a: string, b: string, dir: number): number => {
  if (a === b) return 0;
  if (!a) return 1;
  if (!b) return -1;
  return dir * a.localeCompare(b);
};

export type SortKey = "relevance" | "title" | "newest" | "oldest";
export const SORTS: { key: SortKey; label: string }[] = [
  { key: "relevance", label: "Best match" },
  { key: "newest", label: "Newest" },
  { key: "oldest", label: "Oldest" },
  { key: "title", label: "Title A–Z" },
];

// Order results for display. "relevance" preserves the caller's order (the MiniSearch score order,
// or the facet-count order when there's no query) — so it's the identity. The others return a NEW
// array (never mutate the input). Items without a publication date sort after those with one under
// both date orders, so an undated pub or a freshly reloaded layer never jumps to the top of "Newest";
// layers still order among themselves by when they were loaded.
export function sortItems(items: ItemRef[], key: SortKey): ItemRef[] {
  if (key === "relevance") return items;
  if (key === "title") return [...items].sort((a, b) => discoveryTitle(a).localeCompare(discoveryTitle(b)));
  const dir = key === "newest" ? -1 : 1;
  return items
    .map((it) => ({ it, published: publishedOf(it), loaded: loadedOf(it) }))
    .sort((a, b) => byDate(a.published, b.published, dir) || byDate(a.loaded, b.loaded, dir))
    .map(({ it }) => it);
}

// "Best match" needs words to rank by; field-only queries (series:GQ) and an empty box have none.
export const ranksByWords = (query: Query): boolean => Boolean(baseTerms(query));

// Without words, "Best match" is just catalog load order, which leads with the oldest external
// publishers, so that view lists newest first instead. A sort the user picked is kept.
export const effectiveSort = (s: DiscoveryState): SortKey =>
  (s.sort === "relevance" && !ranksByWords(parseQuery(s.q)) ? "newest" : s.sort);

// ---- URL <-> Discover state (the boundary) ------------------------------------------------------
// The Discover view's whole filter/sort/layout state lives in the URL so a category tile or a shared
// link reproduces the view. These two pure functions are the validated boundary: parse the raw search
// (all strings, possibly bad) into a typed state, and serialize a state back to a search patch that
// drops defaults (so a pristine view stays a clean `/discover`). Namespaced keys — q / collections
// / types / category / formats / geometry / sort / layout / density / area — never touch App's c/i/l/s.
export type Layout = "gallery" | "list";
export type Density = "comfortable" | "compact";
export type Area = [number, number, number, number];

// An inclusive [min, max]; a null end is open ("Any").
export type Range = [number | null, number | null];

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
  place: string;          // the place an area came from ("Moab"), for its chip; "" for a drawn area
  years: Range | null;    // publication year
  scales: Range | null;   // scale denominators: [24000, 100000] is 1:24,000 to 1:100,000
};

export const DEFAULT_DISCOVERY: DiscoveryState = {
  q: "", collections: [], categories: [], types: [], formats: [],
  geometry: "all", sort: "relevance", layout: "gallery", density: "comfortable", area: null,
  place: "", years: null, scales: null,
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

// "1990,2010" / "1990," / ",2010" → a Range of positive whole numbers (years, scale denominators);
// anything else, or both ends open, → null.
const parseRange = (v: unknown): Range | null => {
  const parts = str(v).split(",");
  if (parts.length !== 2) return null;
  const [lo, hi] = parts.map((p) => (p.trim() === "" ? null : Number(p)));
  if ([lo, hi].some((n) => n !== null && !(Number.isInteger(n) && n > 0))) return null;
  if (lo === null && hi === null) return null;
  return lo !== null && hi !== null && lo > hi ? [hi, lo] : [lo, hi];
};
const rangeParam = (r: Range | null): string | undefined =>
  (r ? `${r[0] ?? ""},${r[1] ?? ""}` : undefined);

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
    place: parseArea(sp.area) ? str(sp.place) : "",
    years: parseRange(sp.years),
    scales: parseRange(sp.scale),
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
    place: s.area && s.place ? s.place : undefined,
    years: rangeParam(s.years),
    scale: rangeParam(s.scales),
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
  if (s.area) chips.push({ id: "area", label: s.place ? `Near ${s.place}` : "Map area", patch: { area: null, place: "" } });
  if (s.years) chips.push({ id: "years", label: yearsLabel(s.years), patch: { years: null } });
  if (s.scales) chips.push({ id: "scales", label: scalesLabel(s.scales), patch: { scales: null } });
  return chips;
}

const fmtScale = (d: number) => `1:${d.toLocaleString("en-US")}`;
export const yearsLabel = ([lo, hi]: Range): string => {
  if (lo !== null && hi !== null) return lo === hi ? `${lo}` : `${lo} to ${hi}`;
  if (lo !== null) return `${lo} or later`;
  return hi !== null ? `${hi} or earlier` : "Any";
};
export const scalesLabel = ([lo, hi]: Range): string => {
  if (lo !== null && hi !== null) return lo === hi ? fmtScale(lo) : `${fmtScale(lo)} to ${fmtScale(hi)}`;
  if (lo !== null) return `${fmtScale(lo)} or less detailed`;
  return hi !== null ? `${fmtScale(hi)} or more detailed` : "Any";
};

// ---- Year + scale ranges ------------------------------------------------------------------------
// A publication's year; a layer's datetime is when it was loaded, so it has none (datetimeIsPublished).
export const publishedYear = (it: ItemRef): number | null =>
  (datetimeIsPublished(propsOf(it)) ? year(it) : null);

const within = (v: number | null, [lo, hi]: Range): boolean =>
  v !== null && (lo === null || v >= lo) && (hi === null || v <= hi);

// The scales the slider steps through, detailed to broad: the ones UGS maps are published at.
export const SCALE_STEPS = [
  1000, 2400, 6000, 12000, 24000, 31680, 50000, 62500, 100000, 125000, 250000, 500000, 1000000, 2500000,
];
/** The step nearest a denominator, on a log scale (1:30,000 is nearer 1:24,000 than 1:50,000). */
export const nearestStep = (d: number): number => {
  let best = 0;
  SCALE_STEPS.forEach((s, i) => {
    if (Math.abs(Math.log(d / s)) < Math.abs(Math.log(d / SCALE_STEPS[best]))) best = i;
  });
  return best;
};

export type Bin = { lo: number; hi: number; n: number };
/** Counts per `size`-year bin across [min, max], for the year filter's histogram. */
export function yearBins(items: ItemRef[], [min, max]: [number, number], size = 5): Bin[] {
  const bins: Bin[] = [];
  for (let lo = min; lo <= max; lo += size) bins.push({ lo, hi: Math.min(lo + size - 1, max), n: 0 });
  for (const it of items) {
    const y = publishedYear(it);
    if (y !== null && y >= min && y <= max) bins[Math.floor((y - min) / size)].n++;
  }
  return bins;
}
/** Whether a denominator falls in a scale range, compared step by step: the slider and histogram put
 *  a 1:20,000 map at the 1:24,000 step, so a 1:24,000 to 1:24,000 range has to include it too. */
export const inScaleRange = (d: number | null, [lo, hi]: Range): boolean => {
  if (d === null) return false;
  const i = nearestStep(d);
  return (lo === null || i >= nearestStep(lo)) && (hi === null || i <= nearestStep(hi));
};
/** Counts per SCALE_STEPS entry (each item at its nearest step), for the scale filter's histogram. */
export function scaleBins(items: ItemRef[]): Bin[] {
  const bins = SCALE_STEPS.map((s) => ({ lo: s, hi: s, n: 0 }));
  for (const it of items) {
    const d = scaleDenominator(it);
    if (d !== null) bins[nearestStep(d)].n++;
  }
  return bins;
}
/** The span of publication years present, or null when none has one. */
export function yearSpan(items: ItemRef[]): [number, number] | null {
  let min = Infinity, max = -Infinity;
  for (const it of items) {
    const y = publishedYear(it);
    if (y !== null) { min = Math.min(min, y); max = Math.max(max, y); }
  }
  return min <= max ? [min, max] : null;
}

// ---- Every filter, and what each one hides --------------------------------------------------------
export type FilterGroup =
  "collections" | "categories" | "types" | "formats" | "geometry" | "area" | "years" | "scales";

const CLEAR: Record<FilterGroup, Partial<DiscoveryState>> = {
  collections: { collections: [] }, categories: { categories: [] }, types: { types: [] },
  formats: { formats: [] }, geometry: { geometry: "all" }, area: { area: null, place: "" },
  years: { years: null }, scales: { scales: null },
};
const ANY: Record<FilterGroup, string> = {
  collections: "Any collection", categories: "Any category", types: "Any type", formats: "Any format",
  geometry: "With or without a footprint", area: "Anywhere", years: "Any year", scales: "Any scale",
};

export const activeGroups = (s: DiscoveryState): FilterGroup[] => {
  const on: Record<FilterGroup, boolean> = {
    collections: s.collections.length > 0, categories: s.categories.length > 0, types: s.types.length > 0,
    formats: s.formats.length > 0, geometry: s.geometry !== "all", area: s.area !== null,
    years: s.years !== null, scales: s.scales !== null,
  };
  return (Object.keys(on) as FilterGroup[]).filter((g) => on[g]);
};

/** The items passing every Discover filter, or every one but `skip`. */
export function filterResults(items: ItemRef[], s: DiscoveryState, skip?: FilterGroup): ItemRef[] {
  let out = applyFacets(items, {
    collections: skip === "collections" ? [] : s.collections,
    categories: skip === "categories" ? [] : s.categories,
    types: skip === "types" ? [] : s.types,
    formats: skip === "formats" ? [] : s.formats,
    geometry: skip === "geometry" ? "all" : s.geometry,
  });
  if (s.area && skip !== "area") out = filterByViewport(out, s.area);
  const { years, scales } = s;
  if (years && skip !== "years") out = out.filter((it) => within(publishedYear(it), years));
  if (scales && skip !== "scales") out = out.filter((it) => inScaleRange(scaleDenominator(it), scales));
  return out;
}

/** For each active filter, how many more of `matched` would show without it, and the patch that
 *  drops it. Only the ones that would bring something back, most first. */
export type Relief = { group: FilterGroup; label: string; gain: number; patch: Partial<DiscoveryState> };
export function reliefs(matched: ItemRef[], s: DiscoveryState, shown: number): Relief[] {
  return activeGroups(s)
    .map((g) => ({ group: g, label: ANY[g], gain: filterResults(matched, s, g).length - shown, patch: CLEAR[g] }))
    .filter((r) => r.gain > 0)
    .sort((a, b) => b.gain - a.gain);
}
/** Every filter cleared, the query and view settings kept. */
export const CLEAR_ALL: Partial<DiscoveryState> = Object.assign({}, ...Object.values(CLEAR));
