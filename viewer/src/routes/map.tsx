import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useState } from "react";

import { idOf, useViewCtx } from "@/app";
import { ItemMap, type OpenRelated, type RelatedTablesInfo } from "@/map/map";
import { MapDetail } from "@/map/map-detail";
import { LayerList } from "@/map/layer-list";
import { MapLegend } from "@/map/map-legend";
import { RelatedTable } from "@/map/related-table";
import { MapShell } from "@/map/map-shell";
import { colorFor } from "@/map/map-model";
import { relatedAssets } from "@/stac";

function MapView() {
  const c = useViewCtx();
  // A related table opened from a feature popup — shown in the Info dock/sheet in place of the item
  // detail until closed. Scoped to the map screen; the shareable URL still holds the layer set.
  const [related, setRelated] = useState<OpenRelated | null>(null);
  // Drop a stale related view when the open item changes (e.g. clicking a footprint opens a new
  // item) — otherwise the dock keeps showing the previous feature's rows over a changed selection.
  useEffect(() => setRelated(null), [c.itemUrl]);

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
      onOpenRelated={(r) => { setRelated(r); c.revealInfo.current?.(); }} />}
    info={related
      ? <RelatedTable key={`${related.itemHref}::${related.relatedKey}`}
          itemHref={related.itemHref} relatedKey={related.relatedKey} title={related.title}
          props={related.props} onClose={() => setRelated(null)} />
      : <MapDetail item={c.item.data} loading={c.item.isLoading} />}
    layers={<>
      {c.catalog.isLoading && <p className="text-muted-foreground">Loading catalog…</p>}
      <LayerList
        rows={c.layerRows}
        activeIds={c.idsForMap}
        openId={c.itemUrl ? idOf(c.itemUrl) : undefined}
        colorOf={(id: string) => colorFor(c.activeLayers.findIndex((l) => l.id === id))}
        onToggle={c.toggleLayer}
        onToggleMany={c.toggleLayers}
        onOpen={c.openItem}
        // Vector overlays only: a COG/raster tile layer is a picture, not a classification.
        legend={<MapLegend layers={c.activeLayers.flatMap((l, i) => (l.cogHref || l.rasterPmHref ? []
          : [{ id: l.id, title: l.title, color: colorFor(i), styleLayers: c.styleCache[l.id] }]))} />} />
    </>} />;
}

export const Route = createFileRoute("/map")({ component: MapView });
