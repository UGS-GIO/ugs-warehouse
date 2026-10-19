import { useMemo } from "react";
import { type CollectionSummary, type CoverRef, type ItemRef } from "@/catalog/browse";
import { layerCollectionIds } from "@/catalog/catalog";
import { type CatalogDoc } from "@/discover/search-index";
import { CATALOG_URL, childLinks, collKeyOf, itemLinks, type StacDoc, thumbnailAsset, useDocs } from "@/stac";
import { useIndexes } from "@/stac/items-index";

export const itemHrefIn = (collHref: string, id: string) =>
  collHref.replace(/collection\.json(\?.*)?$/, `${encodeURIComponent(id)}/${encodeURIComponent(id)}.json`);

// ---- catalog tree (one level of nesting: root → sub-catalog → series collections) ----
// Counts + titles ride on the child links (warehouse emits ugs:item_count), so the catalog page
// and the series chooser render from a single fetch each — no per-collection fan-out. A
// child whose href ends in catalog.json is a nesting sub-catalog (ugs-publications); the
// rest are leaf collections. (A pre-nesting flat catalog has only leaf collections — this
// still works: ugs-publications is then just a leaf you open into items.)
export function useCatalogTree(catalog: StacDoc | undefined, collectionId: string | undefined) {
  const rootChildren: CollectionSummary[] = childLinks(catalog, CATALOG_URL).map((l) => ({
    id: collKeyOf(l.href) ?? l.href, href: l.href, title: l.title, count: l["ugs:item_count"],
    mappable: l["ugs:mappable_count"],
    kind: l.href.endsWith("/catalog.json") ? "catalog" : "collection",
  }));
  const subCats = rootChildren.filter((c) => c.kind === "catalog");
  const subDocs = useDocs(subCats.map((c) => c.href));
  const seriesChildren: CollectionSummary[] = subCats.flatMap((sc, i) =>
    childLinks(subDocs.docs[i]?.data, sc.href).map((l) => ({
      id: collKeyOf(l.href) ?? l.href, href: l.href, title: l.title,
      count: l["ugs:item_count"], mappable: l["ugs:mappable_count"], kind: "collection", parentId: sc.id,
    })));
  const leafColls = [...rootChildren.filter((c) => c.kind === "collection"), ...seriesChildren];
  const layerCollIds = layerCollectionIds(rootChildren, seriesChildren);

  const subCat = subCats.find((c) => c.id === collectionId);
  const leafColl = leafColls.find((c) => c.id === collectionId);
  // Cards for the current browse level: none at a leaf (items show); a sub-catalog's series; else root.
  const cards = leafColl ? [] : subCat ? seriesChildren.filter((c) => c.parentId === subCat.id) : rootChildren;
  return { rootChildren, subCats, seriesChildren, leafColls, layerCollIds, subCat, leafColl, cards };
}

// Each collection's items from its items.json. Graceful fallback: a collection whose items.json is
// missing (pre-index catalog) fetches its collection.json for item links, then those items —
// scoped, never a catalog-wide fan-out. `loadKey` says how much has loaded.
export function useIndexedItems(colls: { id: string; href: string }[]) {
  const idx = useIndexes(colls);
  const fbColls = colls.filter((_, i) => idx[i]?.missing);
  const fbCollDocs = useDocs(fbColls.map((c) => c.href));
  const fallbackRefs = fbColls.flatMap((c, i) =>
    itemLinks(fbCollDocs.docs[i]?.data, c.href).map((l) => ({ collId: c.id, href: l.href })));
  const fbDocs = useDocs(fallbackRefs.map((r) => r.href));
  const items: ItemRef[] = [
    ...idx.flatMap((r) => (r.index?.items ?? []).map((d) => ({
      collId: r.id, href: itemHrefIn(r.href, String(d.id)), data: d,
    }))),
    ...fallbackRefs.map((r, i) => ({ ...r, data: fbDocs.docs[i]?.data })),
  ];
  return {
    items,
    isLoading: idx.some((r) => r.isLoading) || fbDocs.isLoading,
    loadKey: idx.map((r) => `${r.id}:${r.index?.items?.length ?? 0}`).join("|")
      + `|fb:${fbDocs.docs.filter((d) => d?.data).length}`,
  };
}

// ---- cover strips on the collection cards (latest covers, newest first) ----
// Fetches every given leaf's items.json so a sub-catalog card (Publications) can aggregate
// latest-across-series. Shares the ["index", href] cache with the item-list fetch, so overlapping
// leaves load once.
export function useCovers(coverColls: CollectionSummary[], cards: CollectionSummary[], seriesChildren: CollectionSummary[]) {
  const coverIdx = useIndexes(coverColls);
  const coverKey = coverIdx.map((r) => `${r.id}:${r.index?.items?.length ?? 0}`).join("|");
  const coversByColl = useMemo(() => {
    const out: Record<string, CoverRef[]> = {};
    for (const r of coverIdx) {
      const date = (d: StacDoc) => String(d.properties?.datetime ?? "");
      out[r.id] = (r.index?.items ?? [])
        .map((d) => ({ d, th: thumbnailAsset(d) }))
        .filter((x) => x.th)
        .sort((a, b) => date(b.d).localeCompare(date(a.d)))
        .slice(0, 5)
        .map(({ d, th }) => ({
          href: itemHrefIn(r.href, String(d.id)), thumb: th!.href,
          title: String(d.properties?.title ?? d.id ?? ""),
          date: date(d),
        }));
    }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [coverKey]);

  // Every catalog item flattened for the Search view (title + id + keywords + topic), so search
  // covers all pubs + map layers, not just Survey Notes. Same indexes the cover strips load.
  const catalogDocs = useMemo<CatalogDoc[]>(() =>
    coverIdx.flatMap((r) => (r.index?.items ?? []).map((d) => {
      const p = (d.properties ?? {}) as Record<string, unknown>;
      return {
        id: `${r.id}/${d.id}`, collId: r.id, itemId: String(d.id),
        title: String(p.title ?? d.id),
        keywords: String(p.keywords ?? ""),
        meta: [p["ugs:series"], p["ugs:topic"], p["ugs:pub_type"]].filter(Boolean).join(" · "),
      };
    })),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [coverKey]);

  // Attach covers to each card: a leaf uses its own; a sub-catalog (Publications) merges its series'
  // covers and re-sorts newest-first across all of them.
  const cardsWithCovers = useMemo<CollectionSummary[]>(() => cards.map((c) => {
    const covers = c.kind === "catalog"
      ? seriesChildren.filter((s) => s.parentId === c.id).flatMap((s) => coversByColl[s.id] ?? [])
          .sort((a, b) => (b.date ?? "").localeCompare(a.date ?? "")).slice(0, 5)
      : coversByColl[c.id] ?? [];
    return covers.length ? { ...c, covers } : c;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [cards, coversByColl]);
  return { catalogDocs, cardsWithCovers };
}
