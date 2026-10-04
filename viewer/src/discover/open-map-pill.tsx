// A persistent "Open map · N layers →" affordance for Discovery. Adding a layer from a card appends
// to ?l=, but Discovery's own map only draws footprints — so the add is otherwise invisible here.
// This shows the running count of what you've queued and the way to the Map view, where the layers
// actually render. Hidden until at least one layer is added.
import { useViewCtx } from "@/app";

export function OpenMapPill() {
  const c = useViewCtx();
  const n = (c.layerIds ?? []).length;
  if (!n) return null;
  return (
    <button type="button" onClick={() => c.setView("map")}
      title="Open the map with your added layers"
      className="absolute bottom-4 left-1/2 z-30 flex -translate-x-1/2 items-center gap-2 rounded-full bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-lg hover:opacity-90">
      Open map · {n} layer{n === 1 ? "" : "s"} →
    </button>
  );
}
