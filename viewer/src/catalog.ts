// Pure catalog-tree helpers (no browser globals, so unit-testable in node).

// Sub-catalogs whose children are map-layer collections rather than publication series. Vector
// serving topics split into one collection per dbt mart schema, so their layers are nested a level
// down — without this they'd read as pubs and drop out of the layer set.
const LAYER_CATALOG_IDS = new Set(["ugs-serving-topics"]);

// Map-layer collections = root leaf collections, plus the children of a layer sub-catalog
// (excluded from the by-date "All items" list). Other sub-catalogs (publications) are pubs.
export const layerCollectionIds = (
  rootChildren: { id: string; kind?: string }[],
  nestedChildren: { id: string; parentId?: string }[] = [],
): string[] => [
  ...rootChildren.filter((c) => c.kind === "collection").map((c) => c.id),
  ...nestedChildren.filter((c) => c.parentId && LAYER_CATALOG_IDS.has(c.parentId)).map((c) => c.id),
];
