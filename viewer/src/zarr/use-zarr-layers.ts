/**
 * Builds the deck.gl layers for a set of datacubes. Shared by the item-detail pane and the map view
 * so both open stores, derive the stretch, and colour the result identically.
 */
import type { Layer, LayersList } from "@deck.gl/core";
import { qk } from "@/query-keys";
import { createColormapTexture, decodeColormapSprite, COLORMAP_INDEX } from "@developmentseed/deck.gl-raster/gpu-modules";
import colormapsPng from "@developmentseed/deck.gl-raster/gpu-modules/colormaps.png?url";
import { ZarrLayer } from "@developmentseed/deck.gl-zarr";
import type { Device } from "@luma.gl/core";
import { useQueries, useQuery } from "@tanstack/react-query";
import { useMemo, useRef, useState } from "react";

import type { CubeStep } from "@/stac";
import { makeLocalEpsgResolver } from "./epsg";
import { openZarr, sampleRange, type ZarrSource } from "./store";
import { makeGetTileData, makeRenderTile } from "./tile";

/** `selection` = an index for every non-spatial dim; ZarrLayer throws unless all are pinned. */
export type ZarrSpec = { id: string; href: string; variable: string; selection: Record<string, number> };

// STAC can list more steps than the store holds (a catalog updated ahead of its data).
const dimLength = (src: ZarrSource, dim: string): number | undefined => {
  const i = src.array.dimensionNames?.indexOf(dim) ?? -1;
  return i >= 0 ? src.array.shape[i] : undefined;
};

function useColormapTexture(device: Device | null) {
  const { data: sprite } = useQuery({
    queryKey: qk.colormapSprite,
    queryFn: async () => decodeColormapSprite(await (await fetch(colormapsPng)).arrayBuffer()),
    staleTime: Infinity,
  });
  return useMemo(
    () => (device && sprite ? createColormapTexture(device, sprite) : null),
    [device, sprite],
  );
}

/** Opening a cube, as one query definition: the overlay draws from it and the layer list reads its
 *  status from the same cache entry, so neither opens the store twice. */
export const zarrSourceQuery = (s: Pick<ZarrSpec, "href" | "variable">) => ({
  queryKey: qk.zarrSource(s.href, s.variable),
  queryFn: async () => {
    const src = await openZarr(s.href, s.variable);
    return { src, range: await sampleRange(src) };
  },
  retry: false,
  staleTime: Infinity,
});

/**
 * Why each cube failed to open, for the layer list: without it a cube that cannot load sits "on"
 * in the list, with a legend, drawing nothing. Reads the overlay's own cache entries.
 */
export function useZarrProblems(specs: Pick<ZarrSpec, "id" | "href" | "variable">[]): Record<string, string> {
  const results = useQueries({ queries: specs.map(zarrSourceQuery) });
  return Object.fromEntries(specs.flatMap((s, i) => {
    const e = results[i]?.error;
    return e ? [[s.id, describeZarrError(e)]] : [];
  }));
}

/** A person-readable reason. A store with nothing at its address is the common case: the catalog
 *  item exists but its data was never published. */
export function describeZarrError(e: Error): string {
  return /404|not found|no such|NoSuchKey/i.test(e.message)
    ? "No data is published for this datacube yet."
    : `Could not open the datacube: ${e.message}`;
}

/** STAC's steps per dim, cut to what the opened store actually holds. Reads the overlay's cache. */
export function useCubeSteps(
  spec: Pick<ZarrSpec, "href" | "variable">,
  stepDims: Record<string, CubeStep[]>,
): Record<string, CubeStep[]> {
  const src = useQuery({ ...zarrSourceQuery(spec), enabled: Boolean(spec.variable) }).data?.src;
  return useMemo(() => (src
    ? Object.fromEntries(Object.entries(stepDims).map(([d, steps]) => [d, steps.slice(0, dimLength(src, d))]))
    : stepDims), [src, stepDims]);
}

export interface ZarrLayersResult {
  // LayersList, not ZarrLayer[] — ZarrLayer's generics resolve to our tile-data shape, which isn't
  // assignable to the class's default instantiation.
  layers: LayersList;
  states: { isLoading: boolean; error: Error | null }[];
}

export function useZarrLayers(specs: ZarrSpec[], device: Device | null): ZarrLayersResult {
  const colormapTexture = useColormapTexture(device);

  const results = useQueries({ queries: specs.map(zarrSourceQuery) });

  // Query objects are new every render, so the memo keys off the specs and each query's settled
  // state instead. Rebuilding a ZarrLayer needlessly re-reads chunks.
  const specKey = specs.map((s) => `${s.id}:${s.href}:${s.variable}:${selKey(s.selection)}`).join("|");
  const readyKey = results.map((r) => (r.data ? "1" : r.error ? "e" : "0")).join("");

  // The last layer per cube whose viewport finished loading. A step or variable change draws it
  // under its replacement until that one has loaded too, so the map swaps pictures, never blanks.
  const [loaded, setLoaded] = useState<Record<string, string>>({});
  // Built layers by id, reused so a re-render doesn't hand deck a fresh instance for the same tiles.
  const built = useRef(new Map<string, Layer>());

  const layers = useMemo(() => {
    if (!colormapTexture) return [];
    const keep = new Set<string>();
    const out = specs.flatMap((s, i) => {
      const data = results[i]?.data;
      const held = loaded[s.id] ? built.current.get(loaded[s.id]) : undefined;
      if (!data) {
        if (held) keep.add(held.id);
        return held ? [held] : [];
      }
      const { src, range } = data;
      const selection = Object.fromEntries(Object.entries(s.selection)
        .map(([d, i]) => [d, Math.min(i, (dimLength(src, d) ?? i + 1) - 1)]));
      // The step is in the id: the tile cache never refetches on a selection change alone.
      const id = `zarr-${s.id}-${s.variable}-${selKey(selection)}`;
      const layer = built.current.get(id) ?? new ZarrLayer({
        id,
        node: src.array,
        // One index per non-spatial dim, so what reaches the GPU is a 2D (y, x) slice.
        selection,
        opacity: 0.85,
        epsgResolver: makeLocalEpsgResolver(src.arrayAttrs),
        getTileData: makeGetTileData(src.noDataValue),
        renderTile: makeRenderTile({
          colormapTexture,
          colormapIndex: COLORMAP_INDEX.viridis,
          noDataValue: src.noDataValue,
          rescaleMin: range[0],
          rescaleMax: range[1],
        }),
        onViewportLoad: () => setLoaded((p) => (p[s.id] === id ? p : { ...p, [s.id]: id })),
      });
      built.current.set(id, layer);
      keep.add(id);
      if (held && held.id !== id) {
        keep.add(held.id);
        return [held, layer];
      }
      return [layer];
    });
    for (const id of built.current.keys()) if (!keep.has(id)) built.current.delete(id);
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [colormapTexture, specKey, readyKey, loaded]);

  const states = results.map((r) => ({ isLoading: r.isLoading, error: r.error ?? null }));
  return { layers, states };
}

const selKey = (sel: Record<string, number>) => Object.entries(sel).map(([d, i]) => `${d}=${i}`).join(",");
