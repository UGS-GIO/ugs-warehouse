/**
 * One interleaved deck.gl overlay carrying BOTH zarr datacube layers and COG layers, so they share the
 * map's single GL context (two overlays would fight over layer-group insertion). Lazy-loaded by the
 * map, so a view with no datacube or COG never pulls deck.gl-zarr / deck.gl-geotiff.
 *
 * COG layers position in the maplibre stack via their `beforeId` (tray order, under vectors); zarr
 * datacubes carry none, so they land in deck's top group as before. COGLayer needs no `device` (it
 * loads its own GeoTIFF + textures), so it composes freely alongside the zarr layers that do.
 */
import type { Device } from "@luma.gl/core";
import { useState } from "react";

import { DeckOverlay } from "@/zarr/zarr-overlay";
import { useZarrLayers, type ZarrSpec } from "@/zarr/use-zarr-layers";
import { buildCogLayer, type CogBounds } from "./cog";

/** A COG to draw: a stable `id`, its href, and the maplibre layer id it should draw under (tray order). */
export type CogSpec = { id: string; href: string; beforeId?: string };

export function MapDeckLayers({
  zarrSpecs = [],
  cogSpecs = [],
  onCogBounds,
}: {
  zarrSpecs?: ZarrSpec[];
  cogSpecs?: CogSpec[];
  onCogBounds?: (href: string, b: CogBounds) => void;
}) {
  const [device, setDevice] = useState<Device | null>(null);
  const { layers: zarrLayers } = useZarrLayers(zarrSpecs, device);
  // Rebuild a COGLayer per spec each render; deck diffs by id + (stable) props, so no re-fetch.
  // cogSpecs arrives bottom→top, so the array order gives the right intra-group order for stacked COGs.
  const cogLayers = cogSpecs.map((c) =>
    buildCogLayer({ id: c.id, href: c.href, beforeId: c.beforeId, onBounds: (b) => onCogBounds?.(c.href, b) }),
  );
  // COGs first so that a top-of-tray COG (no beforeId) and the datacubes (also no beforeId) both land
  // in deck's top group with zarr drawn ABOVE the COG, matching zarr's existing "floats on top of the
  // raster" model. (This pair isn't tray-ordered; a COG with a beforeId sits in the stack regardless.)
  return <DeckOverlay layers={[...cogLayers, ...zarrLayers]} onDeviceInitialized={setDevice} />;
}

/** Single-COG deck overlay for the item-detail preview (no zarr, no tray, so no `beforeId`). */
export function PreviewCogOverlay({ href, onBounds }: { href: string; onBounds?: (b: CogBounds) => void }) {
  return <DeckOverlay layers={[buildCogLayer({ id: "cog", href, onBounds })]} />;
}
