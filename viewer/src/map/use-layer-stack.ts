import { type RefObject, useEffect } from "react";
import type { MapRef } from "react-map-gl/maplibre";

// Keep drawn GL layers in `bottomToTop` order. react-map-gl adds each <Layer> on top when it mounts
// and only calls moveLayer when its beforeId prop changes, so layers that mount at different times
// stack in mount order. This sends each present layer to the top in turn, on every idle, which also
// places a layer that mounts late. A computed beforeId is avoided: maplibre's moveLayer takes the
// layer out of the order before checking the target exists, so a target not yet added drops it.
export function useLayerStack(mapRef: RefObject<MapRef | null>, bottomToTop: string[], mapLoaded: boolean): void {
  const key = bottomToTop.join(",");
  useEffect(() => {
    const map = mapRef.current?.getMap();
    if (!map || !bottomToTop.length) return;
    const reconcile = () => {
      if (!map.isStyleLoaded()) return;
      const desired = bottomToTop.filter((id) => map.getLayer(id));
      // `idle` fires after every pan and zoom; skip the moves when the stack is already right.
      const current = map.getLayersOrder().filter((id) => desired.includes(id));
      if (current.length === desired.length && current.every((id, i) => id === desired[i])) return;
      for (const id of desired) map.moveLayer(id);
    };
    reconcile();
    map.on("idle", reconcile);
    return () => { map.off("idle", reconcile); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, mapLoaded]);
}
