import type { Bounds } from "./place-locator";

// How code outside the map (the header search) moves it. The mounted map registers a flyer on
// load. A target set while no map can take it (the view is still switching to /map, or a layer is
// being added and the map will re-fit) waits in `queued` until the map reads it.
let flyer: ((b: Bounds) => boolean) | null = null;
let queued: Bounds | null = null;

export function setFlyer(f: ((b: Bounds) => boolean) | null): void {
  flyer = f;
}

/** Fly now if a loaded map is there, else queue it for the next map that loads or re-fits. */
export function flyTo(b: Bounds): void {
  if (!flyer?.(b)) queued = b;
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
