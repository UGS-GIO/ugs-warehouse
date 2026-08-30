// Pure, unit-tested core for the Discover view: facet extraction + result filtering + sorting over
// the loaded catalog items. Framework/DOM-free — type-only imports of the item + STAC shapes, plus
// the already-pure validBbox from map-model — so it runs in the (node) test env. The view component
// wires these to MiniSearch (the shared search-index) and the live map; neither belongs in this layer.
import type { ItemRef } from "./browse";
import { validBbox } from "./map-model";

// ---- field getters (null-safe; mirror browse.tsx's g* accessors, kept local so this file stays
// framework-free and testable rather than importing the React-heavy Browse module) ----
const propsOf = (it: ItemRef): Record<string, unknown> =>
  (it.data?.properties ?? {}) as Record<string, unknown>;
// The STAC item id (== the publication series id / layer stem), else the item folder from the href.
const itemIdOf = (it: ItemRef): string =>
  String(it.data?.id ?? it.href.split("/").slice(-2)[0] ?? it.href);

export const discoveryTitle = (it: ItemRef): string => String(propsOf(it).title ?? itemIdOf(it));
export const discoverySeries = (it: ItemRef): string => itemIdOf(it);
// collId is the unique collection key (e.g. `ugs-publications/B`); the leaf folder is the label.
export const collectionLabel = (collId: string): string => collId.split("/").pop() ?? collId;
// Coarse "type" for the facet: a publication's type, else a layer's topic, else its series bucket.
export const typeOf = (it: ItemRef): string => {
  const p = propsOf(it);
  return String(p["ugs:pub_type"] ?? p["ugs:topic"] ?? p["ugs:series"] ?? "");
};
// "Has geometry" = a valid lon/lat bbox or an explicit geometry — i.e. it can draw on the map.
export const hasGeometry = (it: ItemRef): boolean =>
  Boolean(validBbox(it.data?.bbox) || it.data?.geometry);

// The doc id bridging a MiniSearch hit (built by search-index.toSearchDoc) back to its item.
export const docIdOf = (it: ItemRef): string => `${it.collId}/${itemIdOf(it)}`;

export type FacetCount = { key: string; label: string; n: number };
export type Facets = { collections: FacetCount[]; types: FacetCount[]; geometry: FacetCount[] };

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
  const types = new Map<string, FacetCount>();
  let has = 0;
  let none = 0;
  for (const it of items) {
    if (!it.data) continue; // not yet loaded → not a facetable result
    if (it.collId) bump(colls, it.collId, collectionLabel(it.collId));
    const t = typeOf(it);
    if (t) bump(types, t, t);
    if (hasGeometry(it)) has++;
    else none++;
  }
  const geometry: FacetCount[] = [];
  if (has) geometry.push({ key: GEOM_HAS, label: "On the map", n: has });
  if (none) geometry.push({ key: GEOM_NONE, label: "No footprint", n: none });
  return { collections: ranked(colls), types: ranked(types), geometry };
}

export type FacetSelection = {
  collections: string[];
  types: string[];
  geometry: "all" | typeof GEOM_HAS | typeof GEOM_NONE;
};

// Keep items matching every active facet group — AND across the groups, OR within a multi-select
// group (collections / types). An item without loaded data is never a result.
export function applyFacets(items: ItemRef[], sel: FacetSelection): ItemRef[] {
  const colls = new Set(sel.collections);
  const types = new Set(sel.types);
  return items.filter((it) => {
    if (!it.data) return false;
    if (colls.size && !colls.has(it.collId)) return false;
    if (types.size && !types.has(typeOf(it))) return false;
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
