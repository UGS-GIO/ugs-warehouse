/**
 * Builds the deck.gl layers for a set of datacubes. Shared by the item-detail pane and the map view
 * so both open stores, derive the stretch, and colour the result identically.
 */
import type { LayersList } from "@deck.gl/core";
import { qk } from "@/query-keys";
import { createColormapTexture, decodeColormapSprite, COLORMAP_INDEX } from "@developmentseed/deck.gl-raster/gpu-modules";
import colormapsPng from "@developmentseed/deck.gl-raster/gpu-modules/colormaps.png?url";
import { ZarrLayer } from "@developmentseed/deck.gl-zarr";
import type { Device } from "@luma.gl/core";
import { useQueries, useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { makeLocalEpsgResolver } from "./epsg";
import { openZarr, sampleRange } from "./store";
import { makeGetTileData, makeRenderTile } from "./tile";

/** `pinDims` = every non-spatial dim; ZarrLayer throws unless all of them are pinned. */
export type ZarrSpec = { id: string; href: string; variable: string; pinDims: string[] };

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
    const e = results[i]?.error as Error | null | undefined;
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
  const specKey = specs.map((s) => `${s.id}:${s.href}:${s.variable}:${s.pinDims.join(",")}`).join("|");
  const readyKey = results.map((r) => (r.data ? "1" : r.error ? "e" : "0")).join("");

  const layers = useMemo(() => {
    if (!colormapTexture) return [];
    return specs.flatMap((s, i) => {
      const data = results[i]?.data;
      if (!data) return [];
      const { src, range } = data;
      return [new ZarrLayer({
        id: `zarr-${s.id}-${s.variable}`,
        node: src.array,
        // First index of every non-spatial dim, so what reaches the GPU is a 2D (y, x) slice.
        selection: Object.fromEntries(s.pinDims.map((d) => [d, 0])),
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
      })];
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [colormapTexture, specKey, readyKey]);

  const states = results.map((r) => ({ isLoading: r.isLoading, error: (r.error as Error | null) ?? null }));
  return { layers, states };
}
