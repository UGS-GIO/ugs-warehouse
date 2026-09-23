import { createFileRoute } from "@tanstack/react-router";

import { idOf, useViewCtx } from "@/app";
import { FeatureDetail, ItemMap, type OpenRelated, type RelatedTablesInfo, type SelectedFeature } from "@/map/map";
import { MapDetail } from "@/map/map-detail";
import { LayerList } from "@/map/layer-list";
import { MapLegend } from "@/map/map-legend";
import { RelatedTable } from "@/map/related-table";
import { MapShell } from "@/map/map-shell";
import { colorForId } from "@/map/map-model";
import { seedStoredArchives } from "@/map/pmtiles-protocol";
import { relatedAssets } from "@/stac";
import { usePerItem } from "@/lib/use-per-item";

// The Info dock shows one of three things, strictly nested: a related table is only reachable from a
// selected feature. No floating feature popup anywhere — stakeholder requirement.
type Dock =
  | { kind: "item" }
  | { kind: "feature"; feature: SelectedFeature }
  | { kind: "related"; feature: SelectedFeature; related: OpenRelated };
const DOCK_ITEM: Dock = { kind: "item" };

function MapView() {
  const c = useViewCtx();
  // Scoped to the open item (usePerItem): opening a footprint changes the item and the dock reads
  // back as `item` in the same render — no reset effect, so no frame where a stale feature shows.
  const [dock, setDock] = usePerItem<Dock>(c.itemUrl ?? "", DOCK_ITEM);

  // A clicked layer's related tables, named from the compact index (its asset summaries carry the
  // related entries). The FK join columns are stripped from the index by design, so RelatedTable
  // reads them from the full item (via `self`) only when one is opened.
  const relatedFor = (layerId: string): RelatedTablesInfo | undefined => {
    const rec = [...c.mapItems, ...c.allItems].find((r) => idOf(r.href) === layerId);
    const tables = relatedAssets(rec?.data).map(({ key, asset }) => ({ key, title: asset.title ?? key }));
    return rec && tables.length ? { itemHref: rec.href, tables } : undefined;
  };

  return <MapShell
    revealInfo={c.revealInfo}
    map={<ItemMap item={c.item.data} layers={c.activeLayers} footprints={c.footprints} onPickFootprint={c.openItem}
      relatedFor={relatedFor}
      onSelectFeature={(f) => { setDock(f ? { kind: "feature", feature: f } : DOCK_ITEM); if (f) c.revealInfo.current?.(); }} />}
    info={dock.kind === "related"
      ? <RelatedTable key={`${dock.related.itemHref}::${dock.related.relatedKey}`}
          itemHref={dock.related.itemHref} relatedKey={dock.related.relatedKey} title={dock.related.title}
          props={dock.related.props} onClose={() => setDock({ kind: "feature", feature: dock.feature })} />
      : dock.kind === "feature"
      ? <FeatureDetail feature={dock.feature}
          onOpenRelated={(r) => { setDock({ kind: "related", feature: dock.feature, related: r }); c.revealInfo.current?.(); }}
          onClose={() => setDock(DOCK_ITEM)} />
      : <MapDetail item={c.item.data} loading={c.item.isLoading} />}
    layers={<>
      {c.catalog.isLoading && <p className="text-muted-foreground">Loading catalog…</p>}
      <LayerList
        rows={c.layerRows}
        activeIds={c.idsForMap}
        openId={c.itemUrl ? idOf(c.itemUrl) : undefined}
        colorOf={colorForId}
        onToggle={c.toggleLayer}
        onToggleMany={c.toggleLayers}
        onReorder={c.setLayerOrder}
        onOpen={c.openItem}
        // Vector PMTiles and the raster mosaics are both single files read by range, so both can
        // be stored whole. A COG goes through the geomatico protocol, which has no file source.
        offlineHrefOf={(id) => {
          const l = c.activeLayers.find((a) => a.id === id);
          return l?.pmHref ?? l?.rasterPmHref;
        }}
        // Vector overlays only: a COG/raster tile layer is a picture, not a classification.
        legend={<MapLegend layers={c.activeLayers.flatMap((l) => (l.cogHref || l.rasterPmHref ? []
          : [{ id: l.id, title: l.title, color: colorForId(l.id), styleLayers: c.styleCache[l.id] }]))} />} />
    </>} />;
}

// Seeding runs in the loader, not in a query: the router awaits it before MapView renders, so a
// downloaded archive is wired into the pmtiles protocol before any source asks for a tile. Seeded
// from a query instead, the map mounts first and resolves against the network, which is exactly
// what fails with no connection. Never rejects — a browser with no OPFS still gets the map.
export const Route = createFileRoute("/map")({
  loader: () => seedStoredArchives().catch(() => 0),
  component: MapView,
});
