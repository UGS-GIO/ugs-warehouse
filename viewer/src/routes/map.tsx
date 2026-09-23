import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";

import { idOf, useViewCtx } from "@/app";
import { FeatureDetail, ItemMap, type OpenRelated, type RelatedTablesInfo, type SelectedFeature } from "@/map/map";
import { MapDetail } from "@/map/map-detail";
import { LayerList } from "@/map/layer-list";
import { MapLegend } from "@/map/map-legend";
import { RelatedTable } from "@/map/related-table";
import { MapShell } from "@/map/map-shell";
import { colorForId } from "@/map/map-model";
import { relatedAssets } from "@/stac";
import { usePerItem } from "@/lib/use-per-item";
import { BasemapDownload } from "@/offline/basemap-download";
import { WhatsHerePicker } from "@/offline/whats-here-picker";
import type { Target } from "@/offline/whats-here";
import { useIsDesktop } from "@/ui/use-breakpoint";

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
  // The map's view, for "This area" in the offline basemap section of the layers panel.
  const [view, setView] = useState<[number, number, number, number] | null>(null);
  // The "What's here" picker. `canSave`: the explicit "Save this area…" always saves; a long press
  // or right-click saves on phones only, and on desktop is for showing what is there.
  const [pick, setPick] = useState<{ target: Target; canSave: boolean } | null>(null);
  const isDesktop = useIsDesktop();

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
      onBoundsChange={setView}
      onPickAt={(lon, lat) => setPick({ target: { kind: "point", lon, lat }, canSave: !isDesktop })}
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
      <BasemapDownload bbox={view}
        onSaveArea={view ? () => setPick({ target: { kind: "area", bbox: view }, canSave: true }) : undefined} />
      {pick && <WhatsHerePicker key={JSON.stringify(pick.target)} target={pick.target} canSave={pick.canSave}
        onClose={() => setPick(null)} />}
      <LayerList
        rows={c.layerRows}
        activeIds={c.idsForMap}
        openId={c.itemUrl ? idOf(c.itemUrl) : undefined}
        colorOf={colorForId}
        onToggle={c.toggleLayer}
        onToggleMany={c.toggleLayers}
        onReorder={c.setLayerOrder}
        onOpen={c.openItem}
        // PMTiles, the raster mosaics and COGs are all single files read by range, and the
        // service worker answers all three out of OPFS, so all three can be stored whole.
        offlineHrefOf={(id) => {
          const l = c.activeLayers.find((a) => a.id === id);
          return l?.pmHref ?? l?.rasterPmHref ?? l?.cogHref;
        }}
        // Vector overlays only: a COG/raster tile layer is a picture, not a classification.
        legend={<MapLegend layers={c.activeLayers.flatMap((l) => (l.cogHref || l.rasterPmHref ? []
          : [{ id: l.id, title: l.title, color: colorForId(l.id), styleLayers: c.styleCache[l.id] }]))} />} />
    </>} />;
}

export const Route = createFileRoute("/map")({ component: MapView });
