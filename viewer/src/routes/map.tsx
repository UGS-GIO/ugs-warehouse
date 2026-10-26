import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";

import { idOf, MapSearchFor, useViewCtx } from "@/app";
import { useIsDesktop } from "@/ui/use-breakpoint";
import { FeatureDetail, ItemMap, type OpenRelated, type RelatedTablesInfo, type SelectedFeature } from "@/map/map";
import { MapDetail } from "@/map/map-detail";
import { LayerList } from "@/map/layer-list";
import { MapLegend } from "@/map/map-legend";
import { RelatedTable } from "@/map/related-table";
import { MapShell } from "@/map/map-shell";
import { colorForId } from "@/map/map-model";
import { isReferenceAsset, relatedAssets } from "@/stac";
import { usePerItem } from "@/lib/use-per-item";
import { BasemapDownload } from "@/offline/basemap-download";
import { WhatsHerePicker } from "@/offline/whats-here-picker";
import { LayerOpacitySlider } from "@/map/layer-opacity-slider";
import type { Target } from "@/offline/whats-here";
import { useZarrProblems } from "@/zarr/use-zarr-layers";
import { LayerCubeControls } from "@/zarr/layer-cube-controls";

// The Info dock shows one of three things, strictly nested: a related table is only reachable from a
// selected feature. No floating feature popup anywhere — stakeholder requirement.
type Dock =
  | { kind: "item" }
  | { kind: "feature"; feature: SelectedFeature }
  | { kind: "related"; feature: SelectedFeature; related: OpenRelated };
const DOCK_ITEM: Dock = { kind: "item" };

function MapView() {
  const c = useViewCtx();
  const isDesktop = useIsDesktop();
  // Scoped to the open item (usePerItem): opening a footprint changes the item and the dock reads
  // back as `item` in the same render — no reset effect, so no frame where a stale feature shows.
  const [dock, setDock] = usePerItem<Dock>(c.itemUrl ?? "", DOCK_ITEM);
  // The map's view, for "This area" in the offline basemap section of the layers panel.
  const [view, setView] = useState<[number, number, number, number] | null>(null);
  // The "What's here" picker. A long press or right-click shows what is there; "Save this area…"
  // opens it straight to saving.
  const [pick, setPick] = useState<{ target: Target; startSaving: boolean } | null>(null);
  // A datacube that can't open says so in its row instead of sitting "on" and blank.
  const zarrProblems = useZarrProblems(c.activeLayers.flatMap((l) => (l.zarr
    ? [{ id: l.id, href: l.zarr.href, variable: l.zarr.variable }] : [])));
  // A datacube's row picks its variable and steps into the URL, which toLayer reads back.
  // An active layer's row: a datacube's picks, else its opacity (the deck overlay has none to set).
  const layerControlsOf = (id: string) => {
    const l = c.activeLayers.find((a) => a.id === id);
    if (!l) return undefined;
    return l.zarr ? <LayerCubeControls id={id} zarr={l.zarr} /> : <LayerOpacitySlider id={id} title={l.title} />;
  };

  // A clicked layer's related tables, named from the compact index (its asset summaries carry the
  // related entries). The FK join columns are stripped from the index by design, so RelatedTable
  // reads them from the full item (via `self`) only when one is opened.
  const relatedFor = (layerId: string): RelatedTablesInfo | undefined => {
    const rec = [...c.mapItems, ...c.allItems].find((r) => idOf(r.href) === layerId);
    const tables = relatedAssets(rec?.data).map(({ key, asset }) =>
      ({ key, title: asset.title ?? key,
        ...(typeof asset["table:row_count"] === "number" ? { rows: asset["table:row_count"] } : {}),
        ...(isReferenceAsset(asset) ? { reference: true } : {}) }));
    return rec && tables.length ? { itemHref: rec.href, tables } : undefined;
  };

  // The picker sits beside the shell, not inside a panel: on phones the panels only render while
  // the sheet is open, so a long press with the sheet down had nowhere to mount it. It portals to
  // the page body either way.
  const picker = pick && <WhatsHerePicker key={JSON.stringify(pick.target)} target={pick.target}
    startSaving={pick.startSaving} onClose={() => setPick(null)} />;

  return <>{picker}<MapShell
    revealInfo={c.revealInfo}
    map={<ItemMap item={c.item.data} layers={c.activeLayers} footprints={c.footprints} onPickFootprint={c.openItem}
      onBoundsChange={setView}
      onPickAt={(lon, lat, zoom) => setPick({ target: { kind: "point", lon, lat, zoom }, startSaving: false })}
      pickedAt={pick?.target.kind === "point" ? { lng: pick.target.lon, lat: pick.target.lat } : null}
      relatedFor={relatedFor}
      search={<MapSearchFor state={c} className={isDesktop ? "w-80" : "w-[calc(100vw-20px)]"} />} showPin
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
        onSaveArea={view ? () => setPick({ target: { kind: "area", bbox: view }, startSaving: true }) : undefined} />
      <LayerList
        rows={c.layerRows}
        activeIds={c.idsForMap}
        openId={c.itemUrl ? idOf(c.itemUrl) : undefined}
        colorOf={colorForId}
        onToggle={c.toggleLayer}
        onToggleMany={c.toggleLayers}
        onReorder={c.setLayerOrder}
        problemOf={(id) => zarrProblems[id]}
        controlsOf={layerControlsOf}
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
    </>} /></>;
}

export const Route = createFileRoute("/map")({ component: MapView });
