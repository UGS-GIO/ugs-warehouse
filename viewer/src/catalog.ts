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

/** An item's OWN OGC API Features endpoint, read from its `rel=service` link.
 *
 * The reverse of building the URL from the item id: the catalog states where an item is served,
 * instead of the viewer guessing. Only the producer knows — the ingest stamps this link on the
 * topics featureserv actually binds, so a raster or publication item has no link and gets no
 * endpoint row, rather than a constructed URL to a collection that never existed (#85).
 *
 * `base` (the ?features= / build-time override) replaces the published host so a session can be
 * pointed at a local service; empty base keeps the published URL as-is.
 */
export const serviceUrlOf = (
  item: { links?: { rel: string; href: string }[] } | undefined,
  base = "",
): string | undefined => {
  const href = (item?.links ?? []).find((l) => l.rel === "service")?.href;
  if (!href) return undefined;
  return base ? href.replace(/^https?:\/\/[^/]+/, base) : href;
};
