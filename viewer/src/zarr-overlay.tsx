/**
 * Datacube layers on the shared map. Lives inside the MapGL tree (useControl needs a map context)
 * and is lazy-loaded, so a map showing only vector/COG layers never pulls in deck.gl-zarr.
 */
import { MapboxOverlay, type MapboxOverlayProps } from "@deck.gl/mapbox";
import type { Device } from "@luma.gl/core";
import { useState } from "react";
import { useControl } from "react-map-gl/maplibre";

import { useZarrLayers, type ZarrSpec } from "./zarr/use-zarr-layers";

/** deck.gl layers as a maplibre control. `interleaved` keeps basemap labels above the raster. */
function DeckOverlay(props: MapboxOverlayProps) {
  const overlay = useControl<MapboxOverlay>(() => new MapboxOverlay({ ...props, interleaved: true }));
  overlay.setProps(props);
  return null;
}

export function ZarrOverlay({ specs }: { specs: ZarrSpec[] }) {
  // The device arrives from deck's own init, so the first render has no colormap texture yet.
  const [device, setDevice] = useState<Device | null>(null);
  const { layers } = useZarrLayers(specs, device);
  return <DeckOverlay layers={layers} onDeviceInitialized={setDevice} />;
}
