import { createFileRoute } from "@tanstack/react-router";

import { idOf, useViewCtx } from "../app";
import { ItemMap } from "../map/map";
import { MapDetail } from "../map/map-detail";
import { LayerList } from "../map/layer-list";
import { MapLegend } from "../map/map-legend";
import { MapShell } from "../map/map-shell";
import { colorFor } from "../map/map-model";

function MapView() {
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
}

export const Route = createFileRoute("/map")({ component: MapView });
