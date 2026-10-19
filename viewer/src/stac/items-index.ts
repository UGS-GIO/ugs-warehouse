import { type QueryClient, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { qk } from "@/query-keys";
import {
  CATALOG_URL, entryCollKeyOf, fetchJson, isCatalogHref, itemHrefIn, LOC, NoIndexError, selfHrefOf,
  type StacDoc,
} from "@/stac";

// ---- compact items index (items.json, one fetch per collection) ----
// Each entry is a mini StacDoc (id, bbox, a props subset, asset + web-map-link summaries)
// — enough to render the list table, facets, and map overlays without N item.json fetches.
// The full item.json stays the source of truth and loads on open (useStac).
// `url` is where useIndexes read the index from, for resolving the entries' relative self links.
export type ItemsIndex = { type?: string; collection?: string | null; count?: number; items: StacDoc[]; url?: string };

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

// An entry without a self link cannot be opened, so it is dropped; the count is logged once per
// loaded index (these run on every render).
const warnedIndexes = new WeakSet<ItemsIndex>();
const warnDropped = (idx: ItemsIndex, kept: number, where: string) => {
  const dropped = idx.items.length - kept;
  if (dropped > 0 && !warnedIndexes.has(idx)) {
    warnedIndexes.add(idx);
    console.warn(`${dropped} entr${dropped === 1 ? "y" : "ies"} without a self link dropped from ${where}`);
  }
};

export const rootIndexItems = (idx: ItemsIndex) => {
  const refs = idx.items.flatMap((d) => {
    const href = selfHrefOf(d, ROOT_INDEX_URL);
    return href ? [{ collId: entryCollKeyOf(href) ?? "", href, data: d }] : [];
  });
  warnDropped(idx, refs.length, ROOT_INDEX_URL);
  return refs;
};

// A loaded index's entries as item refs under collection `id`. A catalog's index (a publication
// series) points at each entry by its self link; a collection's items sit beside it.
export const indexItemRefs = (r: { id: string; href: string; index?: ItemsIndex }) => {
  const idx = r.index;
  if (!idx) return [];
  if (!isCatalogHref(r.href)) {
    return idx.items.map((d) => ({ collId: r.id, href: itemHrefIn(r.href, String(d.id)), data: d }));
  }
  const refs = idx.items.flatMap((d) => {
    const href = idx.url ? selfHrefOf(d, idx.url) : undefined;
    return href ? [{ collId: r.id, href, data: d }] : [];
  });
  warnDropped(idx, refs.length, idx.url ?? r.href);
  return refs;
};

// An index with no item list is an error, so the caller falls back to per-item links.
const validIndex = (doc: ItemsIndex, url: string): ItemsIndex => {
  if (!Array.isArray(doc?.items)) throw new Error(`no items list in ${url}`);
  return doc;
};

// Link rel the warehouse gives a catalog that publishes an items.json.
const INDEX_REL = "ugs-items-index";

// items.json sits next to collection.json (…/<collection>/items.json). A catalog names its own with
// an `ugs-items-index` link (a publication series, or a sub-catalog's rollup); one without it has none.
async function indexUrlFor(href: string, qc: QueryClient): Promise<string> {
  if (!isCatalogHref(href)) return href.replace(/collection\.json(\?.*)?$/, "items.json");
  const cat = await qc.ensureQueryData({ queryKey: qk.stac(href), queryFn: () => fetchJson(href) });
  const link = cat.links?.find((l) => l.rel === INDEX_REL);
  if (!link) throw new NoIndexError(`no ${INDEX_REL} link in ${href}`);
  return new URL(link.href, href).href;
}

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
  const qc = useQueryClient();
  const results = useQueries({
    queries: collections.map((c) => ({
      queryKey: qk.index(c.href),
      // A federated catalog may publish a STAC ItemCollection (`features`).
      queryFn: async () => {
        const url = await indexUrlFor(c.href, qc);
        const doc = await fetchJson(url) as ItemsIndex & { features?: StacDoc[] };
        return { ...validIndex(doc?.features ? { ...doc, items: doc.features } : doc, c.href), url };
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
    error: results[i].error ?? undefined,
  }));
}
