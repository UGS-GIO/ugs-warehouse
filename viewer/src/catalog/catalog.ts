// Pure catalog-tree helpers (no browser globals, so unit-testable in node).
import { idOf, layerLabel, pubOfEntry, type StacDoc } from "@/stac";

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

// One breadcrumb: `to` is where it navigates (`{}` = the catalog root), absent on the current page.
export type Crumb = { label: string; to?: { c?: string; i?: string; keepSeries?: boolean } };
type TrailNode = { id: string; title?: string; parentId?: string };

// Catalog [ / sub-catalog ] [ / series ] [ / publication ] [ / item ]. `leaf` is the open collection,
// or the series of the publication `pub` an open Item sits in.
export const catalogTrail = (o: {
  collectionId?: string; itemUrl?: string; subCat?: TrailNode; subCats: TrailNode[]; leaf?: TrailNode;
  pub?: { sid: string; title?: string };
}): Crumb[] => {
  const { collectionId, itemUrl, subCat, subCats, leaf, pub } = o;
  const crumbs: Crumb[] = [{ label: "Catalog", to: collectionId || itemUrl ? {} : undefined }];
  if (subCat) crumbs.push({ label: subCat.title ?? subCat.id });
  const parent = leaf?.parentId ? subCats.find((s) => s.id === leaf.parentId) : undefined;
  if (parent) crumbs.push({ label: parent.title ?? parent.id, to: { c: parent.id } });
  if (leaf) crumbs.push({ label: leaf.title ?? leaf.id, to: itemUrl ? { c: leaf.id, keepSeries: true } : undefined });
  if (leaf && pub) crumbs.push({ label: pub.title ?? pub.sid, to: { c: leaf.id, i: pub.sid } });
  if (itemUrl) crumbs.push({ label: itemUrl });
  return crumbs;
};

// Map layer-list rows over the loaded items: those that draw, data layers first, then by group and
// title. A publication's layer (tagged in the root index) files under its series and names its
// publication in the title.
export const layerRowsOf = (
  items: { collId: string; href: string; data?: StacDoc }[],
  o: { draws: (d?: StacDoc) => boolean; isLayer: (r: { collId: string; data?: StacDoc }) => boolean;
       collTitle: (id: string) => string },
): { id: string; href: string; title: string; group: string; layer: boolean }[] => items
  .filter((r) => o.draws(r.data))
  .map((r) => {
    const id = idOf(r.href);
    const groupId = pubOfEntry(r.data) ? r.collId.replace(/\/[^/]+$/, "") : r.collId;
    return { id, href: r.href, title: layerLabel(r.data, id), group: o.collTitle(groupId), layer: o.isLayer(r) };
  })
  .sort((a, b) => Number(b.layer) - Number(a.layer)
                  || a.group.localeCompare(b.group) || a.title.localeCompare(b.title));

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

// Both lists are named, so an unknown root shows as "other" rather than defaulting to a publication.
const LAYER_ROOTS = new Set(["ugs-serving-topics", "ugs-rasters", "ugs-geologic-maps"]);
const DOCUMENT_ROOTS = new Set(["ugs-publications", "ugs-external", "ugs-mining-district-files"]);

export type RootGroup = "layers" | "documents" | "federated" | "other";

/** Which landing-page group a root child belongs to. A federated catalog can hold anything. */
export const rootGroupOf = (id: string): RootGroup =>
  /^https?:\/\//.test(id) ? "federated"
    : LAYER_ROOTS.has(id) ? "layers"
    : DOCUMENT_ROOTS.has(id) ? "documents"
    : "other";
