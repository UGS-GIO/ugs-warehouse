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

import { type CubeStep, cubeRenderRescale, stacDocQuery, type StacDoc } from "@/stac";
import { makeLocalEpsgResolver } from "./epsg";
import { openZarr, sampleStretch, type Stretch, type ZarrSource } from "./store";
import { makeGetTileData, makeRenderTile } from "./tile";

/** `selection` = an index for every non-spatial dim; ZarrLayer throws unless all are pinned.
 *  `rescale` = the user's Min/Max; either end falls back to STAC's stretch (`stacRescale`, the item's
 *  render, else the render on the collection at `collectionHref`), else to a sampled one. */
export type ZarrSpec = {
  id: string; href: string; variable: string; selection: Record<string, number>;
  rescale?: [number | undefined, number | undefined];
  stacRescale?: [number, number];
  collectionHref?: string;
};

type StacStretch = { rescale?: [number, number]; settled: boolean };

/** STAC's stretch per spec: the item's render, else its collection's (one cached fetch per
 *  collection). `settled` once that answer is known, so sampling never starts only to be dropped. */
function useStacStretches(specs: Pick<ZarrSpec, "variable" | "stacRescale" | "collectionHref">[]): StacStretch[] {
  const colls = useQueries({
    queries: specs.map((s) => ({
      ...stacDocQuery(s.collectionHref ?? ""),
      enabled: Boolean(s.collectionHref) && !s.stacRescale,
      retry: false,
      staleTime: Infinity,
    })),
  });
  return specs.map((s, i) => {
    if (s.stacRescale) return { rescale: s.stacRescale, settled: true };
    if (!s.collectionHref) return { settled: true };
    const q = colls[i];
    const doc = q?.data as StacDoc | undefined;
    return { rescale: doc ? cubeRenderRescale(doc, s.variable) : undefined, settled: Boolean(q?.data || q?.error) };
  });
}

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
    return { src };
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

/** Sampling a stretch, only for a cube whose STAC names none (item or collection): it reads ~24 steps. */
const zarrStretchQuery = (s: Pick<ZarrSpec, "href" | "variable">, stac: StacStretch, src?: ZarrSource) => ({
  queryKey: qk.zarrStretch(s.href, s.variable),
  queryFn: () => sampleStretch(src!),
  enabled: Boolean(src) && stac.settled && !stac.rescale,
  retry: false,
  staleTime: Infinity,
});

/** The cube's default stretch, for the Min/Max sliders: STAC's when set, else the sampled one. */
export function useCubeStretch(
  spec: Pick<ZarrSpec, "href" | "variable" | "stacRescale" | "collectionHref">,
): Stretch | undefined {
  const src = useQuery({ ...zarrSourceQuery(spec), enabled: Boolean(spec.variable) }).data?.src;
  const [stac] = useStacStretches([spec]);
  const sampled = useQuery(zarrStretchQuery(spec, stac, src)).data;
  // Stable identity: callers hand it to effects, and a fresh object each render loops them.
  const [lo, hi] = stac.rescale ?? [];
  return useMemo(
    () => (lo !== undefined && hi !== undefined ? { range: [lo, hi] as [number, number] } : sampled),
    [lo, hi, sampled],
  );
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
  const stac = useStacStretches(specs);
  const sampled = useQueries({ queries: specs.map((s, i) => zarrStretchQuery(s, stac[i], results[i]?.data?.src)) });

  // Query objects are new every render, so the memo keys off the specs and each query's settled
  // state instead. Rebuilding a ZarrLayer needlessly re-reads chunks.
  const specKey = specs.map((s, i) => `${s.id}:${s.href}:${s.variable}:${selKey(s.selection)}:${s.rescale?.join("~")}:${stac[i]?.rescale}`)
    .join("|");
  const readyKey = results.map((r) => (r.data ? "1" : r.error ? "e" : "0")).join("")
    + sampled.map((r) => (r.data ? "1" : "0")).join("");

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
      const held = loaded[s.id] ? heldLayer(built.current, loaded[s.id]) : undefined;
      if (!data) {
        if (held) keep.add(held.key);
        return held ? [held.layer] : [];
      }
      const { src } = data;
      const base = stac[i]?.rescale ?? sampled[i]?.data?.range;
      if (!base) {
        if (held) keep.add(held.key);
        return held ? [held.layer] : [];
      }
      const rescale: [number, number] = [s.rescale?.[0] ?? base[0], s.rescale?.[1] ?? base[1]];
      const selection = Object.fromEntries(Object.entries(s.selection)
        .map(([d, step]) => [d, Math.min(step, (dimLength(src, d) ?? step + 1) - 1)]));
      // The step is in the id: the tile cache never refetches on a selection change alone.
      const id = `zarr-${s.id}-${s.variable}-${selKey(selection)}`;
      // Cached per stretch too: a Min/Max change rebuilds the instance but keeps the id, so deck
      // re-colours the loaded tiles (updateTriggers) instead of refetching them.
      const cacheKey = `${id}|${rescale.join("~")}`;
      const layer = built.current.get(cacheKey) ?? new ZarrLayer({
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
          rescaleMin: rescale[0],
          rescaleMax: rescale[1],
        }),
        updateTriggers: { renderTile: rescale },
        onViewportLoad: () => setLoaded((p) => (p[s.id] === id ? p : { ...p, [s.id]: id })),
      });
      built.current.set(cacheKey, layer);
      keep.add(cacheKey);
      if (held && held.layer.id !== id) {
        keep.add(held.key);
        return [held.layer, layer];
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

// The newest built instance for a layer id (a stretch change adds instances under the same id).
function heldLayer(built: Map<string, Layer>, id: string): { key: string; layer: Layer } | undefined {
  const keys = [...built.keys()].filter((k) => k.startsWith(`${id}|`));
  const key = keys.at(-1);
  return key ? { key, layer: built.get(key)! } : undefined;
}

const selKey = (sel: Record<string, number>) => Object.entries(sel).map(([d, i]) => `${d}=${i}`).join(",");
