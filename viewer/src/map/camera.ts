import type { MapRef } from "react-map-gl/maplibre";
import type { Bounds } from "./place-locator";

// How code outside the map (the header search) moves it. The map registers its ref object on load;
// react-map-gl empties the ref on unmount, so nothing of an unmounted map stays reachable. A target
// set while no map can take it (the view is still switching to /map, or a layer is being added and
// the map will re-fit) waits in `queued` until the map reads it.
let target: { current: MapRef | null } | null = null;
let queued: Bounds | null = null;

export function setMapTarget(ref: { current: MapRef | null }): void {
  target = ref;
}

export function fitTo(map: MapRef, [w, s, e, n]: Bounds): void {
  map.fitBounds([[w, s], [e, n]], { padding: 40, maxZoom: 14, duration: 800 });
}

/** Fly now if a map is mounted, else queue it for the next map that loads or re-fits. */
export function flyTo(b: Bounds): void {
  const map = target?.current;
  if (map) fitTo(map, b);
  else queued = b;
}

/** Queue a target for the map's next fit, which it takes instead of fitting its layers. */
export function queueFocus(b: Bounds): void {
  queued = b;
}

export function takeFocus(): Bounds | null {
  const b = queued;
  queued = null;
  return b;
}
