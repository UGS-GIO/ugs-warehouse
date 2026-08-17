/**
 * Item-detail pane for a zarr datacube. Renders any item whose asset is a store — no per-dataset
 * code: `cube:variables` names what is drawable, `cube:dimensions` says which axis is time, and the
 * store's own GeoZarr `spatial:*` / `proj:wkt2` attrs place it. Variable and step are pinned to the
 * first of each for now (#139 phase 2 adds the pickers).
 */
import type { Device } from "@luma.gl/core";
import { MapboxOverlay, type MapboxOverlayProps } from "@deck.gl/mapbox";
import maplibregl from "maplibre-gl";
import { useMemo, useState } from "react";
import { Map as MapGL, NavigationControl, useControl } from "react-map-gl/maplibre";

import { type Asset, cubeVariables, nonSpatialDimensions, type StacDoc, timeDimensionOf } from "./stac";
import { useZarrLayers } from "./zarr/use-zarr-layers";

const POSITRON = "https://tiles.openfreemap.org/styles/positron";

function DeckOverlay(props: MapboxOverlayProps) {
  const overlay = useControl<MapboxOverlay>(() => new MapboxOverlay({ ...props, interleaved: true }));
  overlay.setProps(props);
  return null;
}

export function ZarrMap({ asset, item }: { asset: Asset; item: StacDoc }) {
  const [device, setDevice] = useState<Device | null>(null);

  const variables = useMemo(() => Object.keys(cubeVariables(item)), [item]);
  const variable = variables[0];
  const timeDim = timeDimensionOf(item);
  const id = String(item.id ?? "cube");
  // Every non-spatial dim, not just time — the climatology cubes key on `month`.
  const pinDims = useMemo(() => nonSpatialDimensions(item), [item]);

  const specs = useMemo(
    () => (variable ? [{ id, href: asset.href, variable, pinDims }] : []),
    [id, asset.href, variable, pinDims],
  );
  const { layers, states } = useZarrLayers(specs, device);
  const state = states[0];

  if (!variable) {
    return <Note>This item declares no <code>cube:variables</code>, so there is nothing to draw.</Note>;
  }
  if (state?.error) {
    return <Note tone="error">Could not open the datacube: {state.error.message}</Note>;
  }

  const [w, s, e, n] = (item.bbox ?? [-114.1, 36.9, -108.9, 42.1]).slice(0, 4);
  return (
    <div className="mt-2">
      <div className="h-96 w-full overflow-hidden rounded-md border border-border bg-muted">
        <MapGL
          mapLib={maplibregl}
          initialViewState={{ bounds: [w, s, e, n], fitBoundsOptions: { padding: 20 } }}
          mapStyle={POSITRON}
          style={{ width: "100%", height: "100%" }}
        >
          <NavigationControl position="top-right" showCompass={false} />
          <DeckOverlay layers={layers} onDeviceInitialized={setDevice} />
        </MapGL>
      </div>
      <p className="mt-1.5 text-xs text-muted-foreground">
        {state?.isLoading
          ? "Opening datacube…"
          : <><code>{variable}</code>{timeDim && <> · first {timeDim} step</>} · viridis, 2–98% of a sampled window</>}
        {variables.length > 1 && <> · {variables.length - 1} other variable{variables.length > 2 ? "s" : ""} in this cube</>}
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
