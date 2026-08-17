/**
 * Open a STAC zarr asset as a zarrita array. Two store layouts reach us: an Icechunk repo
 * (versioned — `refs/ snapshots/ manifests/ chunks/`) and a plain zarr v3 hierarchy. Probe for
 * Icechunk first and fall back, so a producer switching layouts doesn't need a viewer change.
 */
import { IcechunkStore } from "icechunk-js";
import * as zarr from "zarrita";

export interface ZarrSource {
  array: zarr.Array<zarr.NumberDataType, zarr.Readable>;
  /** Carries the GeoZarr `spatial:*` / `proj:wkt2` attrs the layer needs to place the raster. */
  arrayAttrs: Record<string, unknown>;
  /** The array's own fill sentinel — masked out on the GPU before the colormap. */
  noDataValue: number;
}

async function openRoot(href: string): Promise<zarr.Group<zarr.Readable>> {
  try {
    // No formatVersion hint — icechunk-js probes `<store>/repo` (v2) then `refs/` (v1).
    const store = await IcechunkStore.open(href, { branch: "main" });
    return await zarr.open.v3(store, { kind: "group" });
  } catch {
    return await zarr.open.v3(new zarr.FetchStore(href), { kind: "group" });
  }
}

export async function openZarr(href: string, variable: string): Promise<ZarrSource> {
  const root = await openRoot(href);
  const node = await zarr.open.v3(root.resolve(variable), { kind: "array" });
  // The render pipeline uploads r32float; an int cube would need its own texture format.
  if (!node.is("float32")) {
    throw new Error(`Variable '${variable}' is ${node.dtype}; only float32 renders today.`);
  }
  return { array: node, arrayAttrs: { ...node.attrs }, noDataValue: fillValueOf(node.attrs) };
}

/** Read the sentinel off the array itself — a sibling's would mask the wrong value. */
export function fillValueOf(attrs: Record<string, unknown>): number {
  const missing = attrs._FillValue ?? attrs.missing_value;
  return typeof missing === "number" && Number.isFinite(missing) ? missing : -9999;
}

/**
 * Colour stretch from one sampled window: a 512² corner of the first step, clipped 2–98%.
 * Approximate by construction, and only needed because STAC carries no statistics for these cubes —
 * a producer-side `raster:bands` statistic should replace it.
 */
export async function sampleRange(src: ZarrSource): Promise<[number, number]> {
  const { array, noDataValue } = src;
  const dims = array.shape.length;
  const [h, w] = array.shape.slice(-2);
  const win = (n: number) => zarr.slice(0, Math.min(n, 512));
  const sel = dims > 2
    ? [...Array<number>(dims - 2).fill(0), win(h), win(w)]   // first index of each non-spatial dim
    : [win(h), win(w)];

  const chunk = await zarr.get(array, sel);
  const vals = Array.from(chunk.data as ArrayLike<number>)
    .filter((v) => Number.isFinite(v) && v !== noDataValue)
    .sort((a, b) => a - b);
  if (vals.length === 0) return [0, 1];

  const at = (q: number) => vals[Math.min(vals.length - 1, Math.floor(q * vals.length))];
  const [lo, hi] = [at(0.02), at(0.98)];
  return hi > lo ? [lo, hi] : [lo, lo + 1];   // flat window — avoid a zero-width stretch
}
