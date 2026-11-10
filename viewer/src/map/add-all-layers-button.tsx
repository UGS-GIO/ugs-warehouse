// "Add all N map layers to the Map": every map layer of a publication into ?l= in one navigation,
// points first so they draw over the lines and polygons. Once all are on, it removes them, and
// "Open the Map" goes there with them.
import { useViewCtx } from "@/app";
import { drawOrder } from "@/catalog/item-view";
import { collectionParts, type StacDoc } from "@/stac";

export function AddAllLayersButton({ item, className = "" }: { item: StacDoc; className?: string }) {
  const ids = drawOrder(collectionParts(item)).map((p) => p.id);
  return ids.length > 1 ? <AddAll ids={ids} className={className} /> : null;
}

function AddAll({ ids, className }: { ids: string[]; className: string }) {
  const c = useViewCtx();
  const allOn = ids.every(c.isActive);
  return (
    <span className={`inline-flex flex-wrap items-center gap-2 ${className}`}>
      <button type="button" aria-pressed={allOn} onClick={() => c.toggleLayers(ids, !allOn)}
        title={allOn ? "Remove these layers from the Map" : "Add every map layer of this publication to the Map"}
        className={`inline-flex items-center gap-1 rounded px-3 py-1.5 text-sm font-medium ${allOn
          ? "border border-primary/30 bg-primary/10 text-primary hover:bg-primary/20"
          : "border border-border bg-card text-foreground hover:border-primary"}`}>
        <span aria-hidden>{allOn ? "✓" : "+"}</span>
        {allOn ? `All ${ids.length} map layers on the Map` : `Add all ${ids.length} map layers to the Map`}
      </button>
      {allOn && (
        <button type="button" onClick={() => c.go({ view: "map", l: c.layerIds, s: c.seriesSel })}
          className="text-sm font-medium text-primary hover:underline">Open the Map ›</button>
      )}
    </span>
  );
}
