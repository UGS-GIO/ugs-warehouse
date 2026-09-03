// The stateful views. Each was a branch of App's ternary; the props are unchanged — only where the
// component lives moved. Shared derived state comes from the layout route via useViewCtx().
import { catalogItemHref, DiscoveryView, idOf, ItemMap, MapDetail, PreviewView, useViewCtx } from "../app";
import { Browse } from "../browse";
import { Landing } from "../landing";
import { LayerList } from "../layer-list";
import { MapLegend } from "../map-legend";
import { MapShell } from "../map-shell";
import { colorFor } from "../map-model";

// The param-less front door. Reuses the same loaded item set as Map/Discover (mapItems) — no extra
// fetch — for its category-tile counts + "recently updated" strip. Everything hands off to Discover.
export const LandingRoute = () => {
  const { mapItems, mapLoadKey, mapItemsLoading, openDiscoverSearch, openInDiscover, openItemPage } = useViewCtx();
  return <Landing
    items={mapItems}
    itemsKey={mapLoadKey}
    loading={mapItemsLoading}
    onSearch={(text: string) => openDiscoverSearch({ q: text })}
    onOpenCategory={(key: string) => openDiscoverSearch({ category: key })}
    onOpenItem={openInDiscover}
    onOpenItemPage={openItemPage} />;
};

// Full-width discovery split — same loaded item set as the map, its own facet rail / cards / synced
// map. Opening a card sets ?i= (staying here); the layout resolves the full doc → the drawer.
export const DiscoverRoute = () => {
  const c = useViewCtx();
  return <DiscoveryView
    items={c.mapItems}
    itemsKey={c.mapLoadKey}
    onOpenItem={c.openItem}
    onOpenItemPage={c.openItemPage}
    itemSelected={Boolean(c.itemUrl)}
    selectedItem={c.item.data}
    selectedCollectionId={c.collectionId}
    onCloseItem={() => c.go({ view: "discover" })}
    onViewOnMap={() => c.go({ view: "map", c: c.collectionUrl, i: c.itemUrl, l: c.itemUrl ? [idOf(c.itemUrl)] : c.layerIds })}
    onExplore={() => c.go({ view: "preview", c: c.collectionUrl, i: c.itemUrl })}
    onFullPage={() => c.go({ view: "catalog", c: c.collectionUrl, i: c.itemUrl })}
    fullPageHref={catalogItemHref(c.collectionUrl, c.itemUrl)} />;
};

// The selected item full-screen, hosting the SAME Preview as the drawer inside the already-mounted
// preview map. Back returns to the Discover drawer it was launched from.
export const PreviewRoute = () => {
  const c = useViewCtx();
  return <PreviewView
    item={c.item.data}
    loading={c.item.isLoading}
    onBack={() => c.go({ view: "discover", c: c.collectionUrl, i: c.itemUrl })}
    onMap={() => c.go({ view: "map", c: c.collectionUrl, i: c.itemUrl, l: c.itemUrl ? [idOf(c.itemUrl)] : c.layerIds })} />;
};

export const CatalogRoute = () => {
  const c = useViewCtx();
  return <Browse
    cards={c.cardsWithCovers}
    collectionId={c.collectionId}
    allItems={c.allItems}
    itemsLoading={c.itemsLoading}
    showItems={Boolean(c.leafColl)}
    atRoot={!c.collectionId}
    breadcrumb={c.crumbs}
    search={c.search}
    onSearch={c.setSearch}
    threeD={c.threeD}
    onThreeD={c.setThreeD}
    browseAll={c.browseAll}
    onBrowseAll={c.setBrowseAll}
    layerCollectionIds={c.layerCollIds}
    series={c.seriesSel ?? []}
    onSeries={c.setSeries}
    item={c.item.data}
    itemSelected={Boolean(c.itemUrl)}
    onOpenCollection={c.openCollection}
    onOpenItem={c.openItem}
    onOpenCover={c.openCover}
    onBackToItems={() => c.go({ view: "catalog", c: c.collectionUrl, s: c.seriesSel })}
    onViewMap={() => c.go({ view: "map", c: c.collectionUrl, i: c.itemUrl, l: c.itemUrl ? [idOf(c.itemUrl)] : c.layerIds })} />;
};

export const MapRoute = () => {
  const c = useViewCtx();
  return <MapShell
    revealInfo={c.revealInfo}
    map={<ItemMap item={c.item.data} layers={c.activeLayers} footprints={c.footprints} onPickFootprint={c.openItem} />}
    info={<MapDetail item={c.item.data} loading={c.item.isLoading} />}
    layers={<>
      {c.catalog.isLoading && <p className="text-muted-foreground">Loading catalog…</p>}
      <LayerList
        rows={c.layerRows}
        activeIds={c.idsForMap}
        openId={c.itemUrl ? idOf(c.itemUrl) : undefined}
        colorOf={(id: string) => colorFor(c.activeLayers.findIndex((l) => l.id === id))}
        onToggle={c.toggleLayer}
        onOpen={c.openItem}
        // Vector overlays only: a COG/raster tile layer is a picture, not a classification.
        legend={<MapLegend layers={c.activeLayers.flatMap((l, i) => (l.cogHref || l.rasterPmHref ? []
          : [{ id: l.id, title: l.title, color: colorFor(i), styleLayers: c.styleCache[l.id] }]))} />} />
    </>} />;
};
