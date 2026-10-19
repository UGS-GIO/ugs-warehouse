import { useQueries, useQuery } from "@tanstack/react-query";
import { qk } from "@/query-keys";
import { CATALOG_URL, collKeyOf, fetchJson, LOC, type StacDoc } from "@/stac";

// ---- compact items index (items.json, one fetch per collection) ----
// Each entry is a mini StacDoc (id, bbox, a props subset, asset + web-map-link summaries)
// — enough to render the list table, facets, and map overlays without N item.json fetches.
// The full item.json stays the source of truth and loads on open (useStac).
export type ItemsIndex = { type?: string; collection?: string | null; count?: number; items: StacDoc[] };

/** The catalog's root items.json: every item of this catalog in one request. */
export const ROOT_INDEX_URL = CATALOG_URL.replace(/catalog\.json(\?.*)?$/, "items.json");

export function useRootIndex(enabled: boolean) {
  return useQuery({
    queryKey: qk.index(ROOT_INDEX_URL),
    queryFn: async () => validIndex(await fetchJson(ROOT_INDEX_URL) as ItemsIndex, ROOT_INDEX_URL),
    enabled,
    retry: false,
  });
}

export const rootIndexItems = (idx: ItemsIndex) => idx.items.flatMap((d) => {
  const self = d.links?.find((l) => l.rel === "self")?.href;
  if (!self) return [];
  const href = new URL(self, ROOT_INDEX_URL).href;
  return [{ collId: collKeyOf(href) ?? "", href, data: d }];
});

// An index with no item list is an error, so the caller falls back to per-item links.
const validIndex = (doc: ItemsIndex, url: string): ItemsIndex => {
  if (!Array.isArray(doc?.items)) throw new Error(`no items list in ${url}`);
  return doc;
};

// items.json sits next to collection.json (…/<collection>/items.json). A nesting sub-catalog may
// publish one too (ugs-serving-topics), rolling up every child collection into one fetch.
const indexUrlFor = (collectionHref: string) =>
  collectionHref.replace(/(collection|catalog)\.json(\?.*)?$/, "items.json");

/** Whether a collection is in this catalog (same origin). */
export const hasItemsIndex = (collectionHref: string, catalogUrl = CATALOG_URL): boolean => {
  try {
    return new URL(collectionHref, LOC.href).origin === new URL(catalogUrl, LOC.href).origin;
  } catch {
    return false;
  }
};

/** Fetch the compact items index for each given collection. Per-collection result carries
 *  the parsed index when present, or an error (e.g. 404 on a pre-index catalog) so the
 *  caller can fall back to per-item fetches. `retry: false` — a 404 is a fast, final miss. */
export function useIndexes(collections: { id: string; href: string }[]) {
  const results = useQueries({
    queries: collections.map((c) => ({
      queryKey: qk.index(c.href),
      // A federated catalog may publish a STAC ItemCollection (`features`).
      queryFn: async () => {
        const doc = await fetchJson(indexUrlFor(c.href)) as ItemsIndex & { features?: StacDoc[] };
        return validIndex(doc?.features ? { ...doc, items: doc.features } : doc, c.href);
      },
      retry: false,
    })),
  });
  return collections.map((c, i) => ({
    id: c.id,
    href: c.href,
    index: results[i].data as ItemsIndex | undefined,
    isLoading: results[i].isLoading,
    missing: Boolean(results[i].error),
  }));
}
