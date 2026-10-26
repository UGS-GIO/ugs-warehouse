import { useMemo, useRef, useState, useTransition } from "react";
import { type ItemRef } from "@/catalog/browse";
import { catalogTrail, crumbNav } from "@/catalog/catalog";
import { useCatalogTree, useCovers, useIndexedItems } from "@/catalog/use-catalog";
import { discoverable } from "@/discover/discovery-model";
import { flyTo, queueFocus, setPin } from "@/map/camera";
import { type ActiveLayer, type Footprint, sheetParam } from "@/map/map-model";
import type { Bounds } from "@/map/place-locator";
import { drawsAsLayer, toLayer } from "@/map/to-layer";
import { useMapItems } from "@/map/use-map-items";
import { useDataSaver } from "@/lib/data-saver";
import { useNav, type View } from "@/shell/nav";
import { useUtahHeader } from "@/shell/utah-header";
import {
  CATALOG_URL, collKeyOf, collectionAsItem, collectionHrefOf, entryCollKeyOf, hasFootprint, idOf, isCatalogHref,
  isCollectionHref, itemHrefIn, itemMissing, pubHrefFor, pubSidIn, resolutionStatus, useStac, zarrAsset,
} from "@/stac";
import { useIndexes } from "@/stac/items-index";
import { useStyleLayersFor } from "@/stac/styles";
import { parseCubeParam } from "@/zarr/cube-picks";

export function useViewState() {
  const { sp, view, collectionUrl, itemUrl, urlLayerIds, seriesSel, go, openDiscoverSearch } = useNav();
  // Tabs: catalog + map share the selection (c/i/l/s); the content views (search/guide) reset
  // it, so the URL stays clean and returning to the catalog doesn't dump you back on an old item.
  // In a transition so switching to a heavy view keeps the current one interactive + flags `pending`.
  const [pending, startTransition] = useTransition();
  const setView = (v: View) =>
    startTransition(() => go(v === "catalog" || v === "map"
      ? { view: v, c: collectionUrl, i: itemUrl, l: layerIds, s: seriesSel }
      : { view: v }));

  useUtahHeader();

  const [search, setSearch] = useState("");
  const [threeD, setThreeD] = useState(false);  // global "3D pubs" discovery filter (loads all items)
  const [browseAll, setBrowseAll] = useState(false);  // flat catalog-wide list, newest-first (loads all items)

  const catalog = useStac(CATALOG_URL);
  const collectionId = collKeyOf(collectionUrl);  // idempotent on a bare key; also handles legacy full-url `c`
  const { rootChildren, subCats, subDocs, seriesChildren, leafColls, layerCollIds, subCat, leafColl, cards } =
    useCatalogTree(catalog.data, collectionId);

  // ---- items for the open leaf, index-driven + lazy (one items.json fetch) ----
  // Loaded for the open leaf, or every leaf while a global search runs.
  // Both search-all and the global 3D filter need every collection's items loaded.
  const searching = !collectionId && (search.trim().length > 0 || threeD || browseAll);
  const wantColls = leafColl ? [leafColl] : searching ? leafColls : [];
  const { items: allItems, isLoading: itemsLoading, idx, lookups } = useIndexedItems(wantColls);
  // A catalog leaf lists its entries only through its `ugs-items-index` link; without one the list
  // says so rather than showing empty.
  const leafIndexMissing = Boolean(leafColl && isCatalogHref(leafColl.href) && idx[0]?.missing);

  // Covers only while cards show (catalog root / a sub-catalog) — not inside a leaf's item list.
  // Decided from the URL, not from `leafColl`: that is unknown until the sub-catalog loads, and in
  // that gap a layer page fetched every collection's index (all 38 publication series, 400 KB+).
  const showsCards = view === "catalog" && !itemUrl && (!collectionId || subCats.some((c) => c.id === collectionId));
  // Data saver shows no covers (catalog/browse.tsx), so it fetches none of the indexes behind them.
  const saver = useDataSaver();
  const { catalogDocs, cardsWithCovers } = useCovers(showsCards && !saver ? leafColls : [], cards, seriesChildren);

  const { mapItems, mapLoadKey, mapItemsLoading, layerIds, layerRows } =
    useMapItems(view, leafColls, layerCollIds, urlLayerIds);
  // Discover lists one entry per publication, not its layers and tables.
  const discoverItems = discoverable(mapItems);

  // i in the URL may be a short id (?i=GQ-1560) or a full STAC URL (older links). Resolve to
  // an absolute href: construct from the open leaf, else look it up among loaded items.
  const isUrl = (s?: string) => Boolean(s) && /^https?:\/\//.test(s as string);

  // Deep-link straight into a sub-catalog that publishes a rollup index — `?c=ugs-serving-topics&i=<id>`.
  // The item lives in a child collection (its mart schema), which the index entry carries, so the
  // href is resolvable without opening that child first. Keeps links minted before the per-schema
  // split working, and is the path the review dashboard's "open item" takes.
  const rollupWanted = Boolean(subCat && !leafColl && itemUrl && !isUrl(itemUrl));
  const rollupIdx = useIndexes(rollupWanted && subCat ? [{ id: subCat.id, href: subCat.href }] : []);
  const rollupHit = rollupIdx[0]?.index?.items?.find((d) => String(d.id) === itemUrl);
  const rollupSchema = rollupHit?.properties?.["ugs:dbt_schema"];
  const rollupHref = subCat && rollupHit && rollupSchema
    ? itemHrefIn(subCat.href.replace(/catalog\.json(\?.*)?$/, `${encodeURIComponent(String(rollupSchema))}/collection.json`),
                 String(rollupHit.id))
    : undefined;

  // Under a publication series (a catalog leaf), `?c=<series>&i=<SID>` is the publication and
  // `?c=<series>/<SID>&i=<item>` an Item inside it; both hrefs are built from the series path.
  const pubSeries = !leafColl && collectionId
    ? leafColls.find((l) => isCatalogHref(l.href) && pubSidIn(l.id, collectionId))
    : undefined;
  const pubSeriesLeaf = leafColl && isCatalogHref(leafColl.href) ? leafColl : pubSeries;
  const pubHref = pubSeriesLeaf && collectionId && itemUrl
    ? pubHrefFor(pubSeriesLeaf, collectionId, itemUrl) : undefined;

  const itemHref = isUrl(itemUrl)
    ? itemUrl
    : (leafColl && itemUrl && !isCatalogHref(leafColl.href) ? itemHrefIn(leafColl.href, itemUrl) : undefined)
      ?? pubHref
      ?? rollupHref
      ?? allItems.find((r) => idOf(r.href) === itemUrl)?.href;
  const itemQuery = useStac(itemHref);
  // A publication Collection reaches every view in the item shape, so the detail page, the map and
  // the add-to-map gate read it like any item. `raw` is the doc as published, for the JSON panel.
  const itemData = useMemo(() => itemQuery.data && collectionAsItem(itemQuery.data), [itemQuery.data]);
  // Every query on the way from `?c=&i=` to an href: the tree, the open collection's index and its
  // collection.json/item-link fallback, and a rollup. The index may be absent (the fallback covers
  // it); the rest failing means the item cannot be looked up, which is not "not found".
  const resolution = resolutionStatus([
    catalog, subDocs, ...lookups,
    ...rollupIdx.map((r) => ({ isLoading: r.isLoading, error: r.error, optional: true })),
  ]);
  const item = {
    data: itemData, raw: itemQuery.data, isLoading: itemQuery.isLoading,
    // Unresolved because a lookup failed: that failure is the item's error (offline, 5xx).
    error: itemQuery.error ?? (itemHref ? undefined : resolution.error),
  };
  // `?i=` names nothing in this catalog: its doc 404s, or every lookup finished cleanly without a match.
  const itemNotFound = itemMissing({ itemUrl, itemHref, error: itemQuery.error,
                                     resolving: resolution.resolving, failed: resolution.error != null });

  // "Part of <publication>" on an Item whose collection is a publication (listed under a series leaf).
  const partOfAbs = item.data && itemHref ? collectionHrefOf(item.data, itemHref) : undefined;
  const partOfSeries = partOfAbs ? entryCollKeyOf(partOfAbs) : undefined;
  const partOfIsPub = Boolean(partOfAbs && isCollectionHref(partOfAbs) && item.data?.type !== "Collection"
    && leafColls.some((l) => l.id === partOfSeries && isCatalogHref(l.href)));
  const partOfDoc = useStac(partOfIsPub ? partOfAbs : undefined);
  const partOf = partOfIsPub && partOfAbs && partOfSeries ? {
    c: partOfSeries, i: idOf(partOfAbs),
    title: String((partOfDoc.data as { title?: unknown } | undefined)?.title ?? idOf(partOfAbs)),
  } : undefined;

  // Open a collection fresh (series filter is per-collection → cleared). Item open / layer
  // toggle / back-to-items keep the active series filter so it survives drilling in + out.
  const revealInfo = useRef<(() => void) | null>(null);   // MapShell hands back "show the detail"
  const openCollection = (href: string) => go({ view, c: collKeyOf(href) });
  // Derive the collection from the item href rather than the ambient collectionUrl — search-all results
  // span collections, so the ambient one is wrong (or absent). Mirrors openCover.
  const openItem = (href: string) => {
    revealInfo.current?.();   // picking on the map raises its detail — the sheet/dock, not a nav
    go({ view, c: entryCollKeyOf(href), i: idOf(href), l: layerIds, s: seriesSel });
  };
  // Open an item straight from a catalog cover strip (no collection open first): derive the leaf
  // collection key from the item href so it resolves + the URL stays tidy.
  const openCover = (href: string) => go({ view, c: entryCollKeyOf(href), i: idOf(href) });
  // The full item page. Below lg this replaces the Discover drawer.
  const openItemPage = (href: string) => go({ view: "catalog", c: entryCollKeyOf(href), i: idOf(href) });
  const setSeries = (codes: string[]) => go({ view, c: collectionUrl, i: itemUrl, l: layerIds, s: codes });
  // Turning a layer ON puts it at the FRONT of the draw order (tray top row = drawn on top); OFF
  // drops it in place. Front-on-add keeps "add to map" coherent with top=front stacking — a newly
  // added layer lands on top, not hidden behind what's already there. The `new Set` also dedupes,
  // so a duplicate id from a hand-edited ?l= self-heals instead of double-mounting a source.
  const toggleLayer = (id: string) => {
    const cur = layerIds ?? [];
    const next = cur.includes(id) ? cur.filter((x) => x !== id) : [id, ...cur];
    go({ view, c: collectionUrl, i: itemUrl, l: [...new Set(next)], s: seriesSel });
  };
  // A whole group at once (a series, a mart schema, or the active set) in ONE navigation, not N:
  // toggleLayer reads `layerIds` from the URL, so a loop over it would drop all but the last id.
  // Adds land at the front too, keeping the batch's own order; removes just drop out. `new Set`
  // dedupes (a self-duplicated batch or a hand-edited ?l= can't double-mount a source).
  const toggleLayers = (ids: string[], on: boolean) => {
    const cur = layerIds ?? [];
    let next: string[];
    if (on) next = [...ids.filter((id) => !cur.includes(id)), ...cur];
    else { const rm = new Set(ids); next = cur.filter((id) => !rm.has(id)); }
    go({ view, c: collectionUrl, i: itemUrl, l: [...new Set(next)], s: seriesSel });
  };
  // "+ Add to map" from a discovery/browse card or the item detail: accumulate into ?l= (never
  // replace), so pulling in a layer while browsing keeps the ones already drawn. `isActive` reads
  // the explicit set the add/remove controls manage; the implicit open-item fallback stays a
  // map-view rendering nicety, unaffected here.
  const addLayer = (id: string) => toggleLayers([id], true);
  // Header search picks. Both land on the map: a place flies there; an item opens its detail, joins
  // the layers when it draws as one, and the map zooms to it rather than to every layer.
  const searchIsLayer = (r: ItemRef) => layerCollIds.includes(r.collId) || !!zarrAsset(r.data);
  const pickPlace = (b: Bounds, label: string, outline?: boolean) => {
    setPin({ lng: (b[0] + b[2]) / 2, lat: (b[1] + b[3]) / 2, label, outline: outline ? b : undefined });
    if (view === "map") flyTo(b);
    else { queueFocus(b); go({ view: "map", l: layerIds, s: seriesSel }); }
  };
  const pickSearchItem = ({ href, bbox }: { href: string; bbox?: Bounds }) => {
    setPin(null);   // an item marks itself with its footprint
    const id = idOf(href);
    const cur = layerIds ?? [];
    const adds = !cur.includes(id) && drawsAsLayer(mapItems.find((r) => r.href === href)?.data);
    if (bbox) { if (view === "map" && !adds) flyTo(bbox); else queueFocus(bbox); }
    revealInfo.current?.();
    go({ view: "map", c: entryCollKeyOf(href), i: id, l: adds ? [id, ...cur] : layerIds, s: seriesSel,
         sheet: sheetParam({ tab: "info", detent: 1 }) });
  };
  const removeLayer = (id: string) => toggleLayers([id], false);
  const isActive = (id: string) => (layerIds ?? []).includes(id);
  // Commit a dragged draw-order back to ?l= (the whole active set stays shareable in the URL).
  const setLayerOrder = (ids: string[]) => go({ view, c: collectionUrl, i: itemUrl, l: ids, s: seriesSel });

  // Breadcrumb trail: Catalog [ / Publications] [ / DS] [ / publication] [ / item]. Each crumb but
  // the last is clickable. An Item inside a publication walks through its series and publication.
  const pubSid = pubSeries && collectionId ? pubSidIn(pubSeries.id, collectionId) : undefined;
  const crumbs = catalogTrail({
    collectionId, itemUrl, subCat, subCats, leaf: leafColl ?? pubSeries,
    pub: pubSid ? { sid: pubSid, title: partOf?.title } : undefined,
    itemTitle: String(item.data?.properties?.title ?? ""),
  }).map(({ label, to }) => ({
    label,
    onClick: to ? () => go(crumbNav(to, view, seriesSel)) : undefined,
  }));

  // Active map layers: the toggled set, else fall back to the detail item (so a plain
  // ?c=&i= link still shows its layer). Resolved against fetched item data (for PMTiles).
  // The compact index records omit `renders`, so prefer the FULL detail item for the open id
  // (it carries the bound style_url) — otherwise the map can't style the selected layer.
  const byId = new Map([...mapItems, ...allItems].map((r) => [idOf(r.href), r]));
  if (item.data && itemUrl) byId.set(idOf(itemUrl), { collId: collectionId ?? "", href: itemHref ?? itemUrl, data: item.data });
  const idsForMap = layerIds ?? (itemUrl ? [idOf(itemUrl)] : []);
  const cubePicks = parseCubeParam(sp.cube);
  const activeLayers = idsForMap.map((id) => toLayer(byId.get(id), cubePicks[id]))
    .filter((l): l is ActiveLayer => l !== null);

  // Coverage overlay data: every loaded item that has a bbox → a footprint rectangle. Lets the map
  // show WHAT IS MAPPED WHERE across the open collection, including items with no COG/PMTiles asset
  // (most of them) that otherwise draw nothing. Scoped to the loaded collection's index (cheap — the
  // bboxes are already fetched); a catalog-wide overlay would need loading every collection's index.
  const footprints: Footprint[] = allItems
    .filter((r) => hasFootprint(r.data))
    .map((r) => ({ href: r.href, id: idOf(r.href),
                   title: String(r.data?.properties?.title ?? idOf(r.href)), bbox: r.data?.bbox }))
    .filter((f): f is Footprint => Array.isArray(f.bbox) && f.bbox.length >= 4);

  // Map view = locked viewport (the map fills the screen, panels scroll internally).
  // Catalog/detail = a document → the page scrolls naturally, header sticks. (No more
  // scroll-box stuck in the middle of an item page.)
  const mapView = view === "map";
  // The Map, Discover and Preview views LOCK the viewport (a full-height split/shell whose inner panes
  // scroll) — unlike the document views (catalog/guide/developers/…), which scroll the page
  // under a sticky header.
  const lockedView = mapView || view === "discover" || view === "preview";
  // Same cached queries the map itself reads (TanStack dedupes by key) — the legend needs the bound
  // style layers, and the drawer renders outside the map component.
  const styleCache = useStyleLayersFor(activeLayers.map((l) => ({ id: l.id, styleUrl: l.styleUrl })));
  // "Is this item a resolvable map layer?" — the gate the Add-to-map button self-checks. `layerRows`
  // only covers the map/discover set (mapColls), so on the item page (catalog view, where
  // mapColls is empty) it's empty. Also treat the OPEN item as a layer when its own loaded doc draws
  // as one — otherwise the item-detail Add-to-map button is permanently hidden there.
  const isLayerId = (id: string) =>
    layerRows.some((r) => r.id === id) || (!!itemUrl && idOf(itemUrl) === id && drawsAsLayer(item.data));
  return {
    go, view, setView, catalog, lockedView, pending, mapView,
    catalogDocs, mapItems, discoverItems, mapLoadKey, mapItemsLoading,
    searchIsLayer, pickPlace, pickSearchItem,
    openItem, openItemPage, openDiscoverSearch, openCollection, openCover,
    itemUrl, item, itemNotFound, partOf, collectionId, collectionUrl, layerIds, seriesSel,
    rootChildren, cardsWithCovers, allItems, itemsLoading, leafIndexMissing, leafColl, crumbs,
    search, setSearch, threeD, setThreeD, browseAll, setBrowseAll,
    layerCollIds, setSeries,
    revealInfo, activeLayers, footprints, layerRows, idsForMap, toggleLayer, toggleLayers, styleCache,
    addLayer, removeLayer, isActive, isLayerId, drawsAsLayer, setLayerOrder,
  };
}
