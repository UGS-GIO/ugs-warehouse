// "+ Add to map" affordance for discovery cards, browse rows, and the item detail. It ADDS the layer
// to the active set (?l=) without leaving the current view — accumulating, never replacing — so
// pulling in a layer while browsing keeps the ones already drawn, and flips to "✓ Added" (click to
// remove) once it's on. The whole active set stays in the shareable URL.
//
// Renders nothing when the item isn't a resolvable map layer (not in the catalog's layer set), so
// ?l= can never gain an id the Layers tray has no row for — every added layer stays manageable.
import type { MouseEvent } from "react";

import { idOf, useViewCtx } from "@/app";

export function AddToMapButton({ layerId, compact = false, large = false }: {
  // The layer to add. Omit ONLY where the component renders inside the open item's own panel (item
  // detail): it then targets the open item — idOf(itemUrl), the authoritative id for that panel.
  // Pass it explicitly (as the cards/rows do) anywhere the shown item may not be the open one.
  layerId?: string;
  compact?: boolean;   // dense chip for a result card/row; default is a detail-panel action button
  large?: boolean;     // the item page's action row
}) {
  const c = useViewCtx();
  const id = layerId ?? (c.itemUrl ? idOf(c.itemUrl) : undefined);
  const isLayer = Boolean(id && c.isLayerId(id));
  if (!id || !isLayer) return null;

  const on = c.isActive(id);
  const click = (e: MouseEvent<HTMLButtonElement>) => {
    // This control always sits BESIDE a card/row link, never inside it — but guard anyway so a stray
    // bubbled click can't also trigger navigation wherever it ends up placed.
    e.preventDefault();
    e.stopPropagation();
    (on ? c.removeLayer : c.addLayer)(id);
  };
  const size = compact ? "px-1.5 py-0.5 text-[11px]" : large ? "px-3 py-1.5 text-sm" : "px-2.5 py-1 text-xs";

  return (
    <button type="button" onClick={click} aria-pressed={on}
      title={on ? "Remove this layer from the map" : "Add this layer to the map"}
      className={`inline-flex items-center gap-1 rounded font-medium ${size} ${on
        ? "border border-primary/30 bg-primary/10 text-primary hover:bg-primary/20"
        : "border border-border bg-card text-foreground hover:border-primary"}`}>
      <span aria-hidden>{on ? "✓" : "+"}</span>
      {on ? "Added" : "Add to map"}
    </button>
  );
}
