/**
 * The shared interleaved deck.gl control for a maplibre map (`interleaved` keeps basemap labels above
 * the raster). Lives inside the MapGL tree, as useControl needs the map context. The components that feed
 * it zarr datacube + COG layers live in map/deck-layers.tsx.
 */
import { MapboxOverlay, type MapboxOverlayProps } from "@deck.gl/mapbox";
import { useControl } from "react-map-gl/maplibre";

export function DeckOverlay(props: MapboxOverlayProps) {
  const overlay = useControl<MapboxOverlay>(() => new MapboxOverlay({ ...props, interleaved: true }));
  overlay.setProps(props);
  return null;
}
