/**
 * Builds deck.gl ZarrLayers for a set of datacube specs. Shared by the item-detail pane and the
 * main map so both open stores, sample the stretch, and colour the result the same way.
 */
import { createColormapTexture, decodeColormapSprite, COLORMAP_INDEX } from "@developmentseed/deck.gl-raster/gpu-modules";
import { ZarrLayer } from "@developmentseed/deck.gl-zarr";
import colormapsPng from "@developmentseed/deck.gl-raster/gpu-modules/colormaps.png?url";
import type { LayersList } from "@deck.gl/core";
import type { Device } from "@luma.gl/core";
import { useQueries, useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { makeLocalEpsgResolver } from "./epsg";
import { openZarr, sampleRange } from "./store";
import { getTileData, makeRenderTile } from "./tile";

/** `pinDims` = every non-spatial dim; ZarrLayer throws unless all of them are pinned. */
export type ZarrSpec = { id: string; href: string; variable?: string; pinDims?: string[] };

/** One sprite holds every colormap's LUT; decode once, upload per device. */
function useColormapTexture(device: Device | null) {
  const { data: sprite } = useQuery({
    queryKey: ["colormap-sprite"],
    queryFn: async () => decodeColormapSprite(await (await fetch(colormapsPng)).arrayBuffer()),
    staleTime: Infinity,
  });
  return useMemo(
    () => (device && sprite ? createColormapTexture(device, sprite) : null),
    [device, sprite],
  );
}

export interface ZarrLayersResult {
  // LayersList, not ZarrLayer[] — ZarrLayer's generics resolve to our tile-data shape, which isn't
  // assignable to the class's default instantiation.
  layers: LayersList;
  /** Per-spec load state, for captions and error surfacing. */
  states: { id: string; variable?: string; isLoading: boolean; error: Error | null; range?: [number, number] }[];
}

export function useZarrLayers(specs: ZarrSpec[], device: Device | null): ZarrLayersResult {
  const colormapTexture = useColormapTexture(device);

  const results = useQueries({
    queries: specs.map((s) => ({
      queryKey: ["zarr-source", s.href, s.variable],
      queryFn: async () => {
        const src = await openZarr(s.href, s.variable!);
        return { src, range: await sampleRange(src) };
      },
      enabled: !!s.variable,
      staleTime: 5 * 60_000,
      retry: false,
    })),
  });

  const layers = useMemo(() => {
    if (!colormapTexture) return [];
    return specs.flatMap((s, i) => {
      const data = results[i]?.data;
      if (!data) return [];
      const { src, range } = data;
      return [
        new ZarrLayer({
          id: `zarr-${s.id}-${s.variable}`,
          node: src.array,
          // First index of every non-spatial dim, so what reaches the GPU is a 2D (y, x) slice.
          selection: Object.fromEntries((s.pinDims ?? []).map((d) => [d, 0])),
          opacity: 0.85,
          epsgResolver: makeLocalEpsgResolver(src.arrayAttrs),
          getTileData,
          renderTile: makeRenderTile({
            colormapTexture,
            colormapIndex: COLORMAP_INDEX.viridis,
            noDataValue: src.noDataValue,
            rescaleMin: range[0],
            rescaleMax: range[1],
          }),
        }),
      ];
    });
    // Depend on the resolved hrefs, not the query objects — those are new every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [colormapTexture, specs.map((s) => `${s.id}:${s.href}:${s.variable}:${(s.pinDims ?? []).join(",")}`).join("|"),
      results.map((r) => (r.data ? "1" : r.error ? "e" : "0")).join("")]);

  const states = specs.map((s, i) => ({
    id: s.id,
    variable: s.variable,
    isLoading: results[i]?.isLoading ?? false,
    error: (results[i]?.error as Error | null) ?? null,
    range: results[i]?.data?.range,
  }));

  return { layers, states };
}
