/**
 * Item-detail pane for a zarr datacube. Generic over any store: `cube:variables` names what is
 * drawable, `cube:dimensions` which axes are non-spatial, and the store's own GeoZarr `spatial:*` /
 * `proj:wkt2` attrs place it. Variable and step are pinned to the first of each (#139 phase 2 adds
 * the pickers).
 */
import type { Device } from "@luma.gl/core";
import maplibregl from "@/map/maplibre-lib";
import { useMemo, useState } from "react";
import { Map as MapGL, NavigationControl } from "react-map-gl/maplibre";

import { type Asset, cubeVariables, nonSpatialDimensions, type StacDoc } from "@/stac";
import { DIRECT, protomapsStyle } from "@/map/basemap-style";
import { ensurePmtilesProtocol } from "@/map/pmtiles-protocol";
import { DeckOverlay } from "./zarr-overlay";
import { useZarrLayers } from "./use-zarr-layers";
import { to2d } from "@/lib/bbox";

ensurePmtilesProtocol();

const LIGHT_BASEMAP = protomapsStyle("white", DIRECT);
const UTAH: [number, number, number, number] = [-114.1, 36.9, -108.9, 42.1];

export function ZarrMap({ asset, item }: { asset: Asset; item: StacDoc }) {
  const [device, setDevice] = useState<Device | null>(null);

  const variables = useMemo(() => Object.keys(cubeVariables(item)), [item]);
  const variable = variables[0];
  const specs = useMemo(
    () => (variable
      ? [{ id: String(item.id ?? "cube"), href: asset.href, variable, pinDims: nonSpatialDimensions(item) }]
      : []),
    [item, asset.href, variable],
  );
  const { layers, states } = useZarrLayers(specs, device);
  const state = states[0];

  if (!variable) {
    return <Note>This item declares no <code>cube:variables</code>, so there is nothing to draw.</Note>;
  }
  if (state?.error) {
    return <Note tone="error">Could not open the datacube: {state.error.message}</Note>;
  }

  const [w, s, e, n] = to2d(item.bbox) ?? UTAH;
  const others = variables.length - 1;
  return (
    <div className="mt-2">
      <div className="h-96 w-full overflow-hidden rounded-md border border-border bg-muted">
        <MapGL
          mapLib={maplibregl}
          initialViewState={{ bounds: [w, s, e, n], fitBoundsOptions: { padding: 20 } }}
          mapStyle={LIGHT_BASEMAP}
          style={{ width: "100%", height: "100%" }}
        >
          <NavigationControl position="top-right" showCompass={false} />
          <DeckOverlay layers={layers} onDeviceInitialized={setDevice} />
        </MapGL>
      </div>
      <p className="mt-1.5 text-xs text-muted-foreground">
        {state?.isLoading
          ? "Opening datacube…"
          : <><code>{variable}</code> · viridis, 2–98% of a sampled window</>}
        {others > 0 && <> · {others} other variable{others > 1 ? "s" : ""} in this cube</>}
      </p>
    </div>
  );
}

function Note({ children, tone }: { children: React.ReactNode; tone?: "error" }) {
  return (
    <div className={`mt-2 rounded-md border border-border bg-muted p-3 text-xs ${tone === "error" ? "text-destructive" : "text-muted-foreground"}`}>
      {children}
    </div>
  );
}
