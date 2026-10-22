import { useMemo } from "react";
import { type CollectionSummary, type ItemRef } from "@/catalog/browse";
import { layerRowsOf } from "@/catalog/catalog";
import { useIndexedItems } from "@/catalog/use-catalog";
import { type LayerRow } from "@/map/layer-list";
import { drawsAsLayer, toLayer } from "@/map/to-layer";
import { useOffline } from "@/offline/store";
import type { View } from "@/shell/nav";
import { zarrAsset } from "@/stac";
import { hasItemsIndex, rootIndexItems, useRootIndex } from "@/stac/items-index";

// The Map and Discover views load every leaf collection's index (all items → the map, the facets
// and the category index). Offline data needs it too, to name what is saved; it is the same set.
export function useMapItems(view: View, leafColls: CollectionSummary[], layerCollIds: string[], urlLayerIds?: string[]) {
  const mapColls = view === "map" || view === "discover" || view === "offline"
    ? leafColls : [];
  // The root index covers this catalog; only federated collections load their own index.
  const root = useRootIndex(mapColls.length > 0);
  const idxColls = root.isLoading ? [] : mapColls.filter((c) => !(root.data && hasItemsIndex(c.href)));
  // Same collection.json → item-links fallback the browse list uses. Without it a federated
  // catalog contributes no layers at all: it publishes no items.json, so the index is empty and
  // its datacubes never reach the layer list. Bounded — only index-less collections take this path.
  const indexed = useIndexedItems(idxColls);
  const mapItems: ItemRef[] = [...(root.data ? rootIndexItems(root.data) : []), ...indexed.items];
  // How much of the map's data has loaded. The fallback count is what federated layers depend on:
  // they arrive only that way, and always after the indexes.
  const mapLoadKey = `root:${root.data?.count ?? 0}|` + indexed.loadKey;
  // Still streaming, so Discover's category index doesn't show counts that are still climbing.
  const mapItemsLoading = mapColls.length > 0 && (root.isLoading || indexed.isLoading);
  // Opened with no layers chosen, the Map shows the ones saved for offline, so a layer saved from
  // its page is on when you come back without a connection. Choosing layers (or none) takes over.
  const offline = useOffline();
  const savedUrls = new Set(offline.files.map((f) => f.url));
  const savedLayerIds = view === "map" && !urlLayerIds && savedUrls.size
    ? mapItems.flatMap((r) => {
        const l = toLayer(r);
        const href = l?.pmHref ?? l?.rasterPmHref ?? l?.cogHref;
        return l && href && savedUrls.has(href) ? [l.id] : [];
      })
    : [];
  const layerIds = urlLayerIds ?? (savedLayerIds.length ? savedLayerIds : undefined);
  // Layer collections first — the serving topics are what the map is for; pub plates come after.
  const collTitle = (id: string) => leafColls.find((c) => c.id === id)?.title ?? id;
  const layerRows: LayerRow[] = useMemo(() => layerRowsOf(mapItems, {
    draws: drawsAsLayer, collTitle,
    // A datacube is a data layer whatever catalog it came from — the sub-catalog allowlist only
    // knows our own ids, so a federated cube would otherwise file under publication plates.
    isLayer: (r) => layerCollIds.includes(r.collId) || !!zarrAsset(r.data),
  }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [mapLoadKey]);
  return { mapItems, mapLoadKey, mapItemsLoading, layerIds, layerRows };
}
