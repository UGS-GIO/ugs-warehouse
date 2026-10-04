/**
 * Datacube layers on a maplibre map. Lives inside the MapGL tree (useControl needs the map context)
 * and is lazy-loaded, so a map with no datacube never pulls in deck.gl-zarr.
 */
import { MapLibreOverlay, type MapLibreOverlayProps } from "@deck.gl/maplibre";
import type { Device } from "@luma.gl/core";
import { useState } from "react";
import { useControl } from "react-map-gl/maplibre";

import { useZarrLayers, type ZarrSpec } from "./use-zarr-layers";

/** deck.gl layers as a maplibre control. `interleaved` keeps basemap labels above the raster. */
export function DeckOverlay(props: MapLibreOverlayProps) {
  const overlay = useControl<MapLibreOverlay>(() => new MapLibreOverlay({ ...props, interleaved: true }));
  overlay.setProps(props);
  return null;
}

export function ZarrOverlay({ specs }: { specs: ZarrSpec[] }) {
  // The device arrives from deck's own init, so the first render has no colormap texture yet.
  const [device, setDevice] = useState<Device | null>(null);
  const { layers } = useZarrLayers(specs, device);
  return <DeckOverlay layers={layers} onDeviceInitialized={setDevice} />;
}
