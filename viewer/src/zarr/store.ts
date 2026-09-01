/**
 * Open a STAC zarr asset as a zarrita array. Two store layouts reach us: an Icechunk repo
 * (versioned — `refs/ snapshots/ manifests/ chunks/`) and a plain zarr v3 hierarchy. Probe for
 * Icechunk first and fall back, so a producer switching layouts doesn't need a viewer change.
 *
 * A variable is likewise either a flat array or a GeoZarr multiscale GROUP of pyramid levels.
 */
import { parseGeoZarrMetadata } from "@developmentseed/geozarr";
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
  const { node, attrs } = await openVariable(root, variable);
  // The render pipeline uploads r32float; an int cube would need its own texture format.
  if (!node.is("float32")) {
    throw new Error(`Variable '${variable}' is ${node.dtype}; only float32 renders today.`);
  }
  return { array: node, arrayAttrs: attrs, noDataValue: fillValueOf(attrs) };
}

/**
 * Resolve a variable to its NATIVE array plus the attrs that place it.
 *
 * A multiscale variable is a group whose children are the pyramid levels, so opening it as an
 * array fails outright ("not found: array at /AET"). `parseGeoZarrMetadata` validates the attrs
 * against the convention and normalises both shapes — a flat variable reports a single level at
 * ".", meaning the variable IS the array.
 *
 * GeoZarr puts `proj:*` / `spatial:*` / `multiscales` on the VARIABLE; in a pyramid the levels
 * themselves carry no attrs at all. Reading them off the level array loses the CRS and the
 * transform, so merge the variable's attrs with the chosen level's own shape/transform.
 */
async function openVariable(
  root: zarr.Group<zarr.Readable>,
  variable: string,
): Promise<{ node: zarr.Array<zarr.NumberDataType, zarr.Readable>; attrs: Record<string, unknown> }> {
  const varNode = await zarr.open.v3(root.resolve(variable));
  const { levels } = parseGeoZarrMetadata(varNode.attrs);
  const native = levels[0];

  const node = (native.path === "."
    ? varNode
    : await zarr.open.v3(varNode.resolve(native.path), { kind: "array" })
  ) as zarr.Array<zarr.NumberDataType, zarr.Readable>;

  const { multiscales: _drop, ...rest } = varNode.attrs;
  const layout = (varNode.attrs.multiscales as { layout?: Record<string, unknown>[] } | undefined)?.layout?.[0];
  return {
    node,
    attrs: layout
      ? { ...rest, "spatial:transform": layout["spatial:transform"], "spatial:shape": layout["spatial:shape"] }
      : { ...rest },
  };
}

/**
 * Read the sentinel off the array itself — a sibling's would mask the wrong value.
 *
 * Zarr v3 cannot put NaN/Infinity in JSON, so a non-finite fill arrives either as the string
 * "NaN"/"Infinity"/"-Infinity" or as base64 of the raw IEEE bytes. Both have to be decoded:
 * treated as an opaque string they fell through to -9999, a value that appears nowhere in the
 * data, so nothing was ever masked and nodata rendered as the colormap floor.
 */
export function fillValueOf(attrs: Record<string, unknown>): number {
  const decoded = decodeFillValue(attrs._FillValue ?? attrs.missing_value);
  return decoded === undefined ? -9999 : decoded;
}

export function decodeFillValue(raw: unknown): number | undefined {
  if (typeof raw === "number") return Number.isFinite(raw) ? raw : raw;
  if (typeof raw !== "string") return undefined;
  if (raw === "NaN") return Number.NaN;
  if (raw === "Infinity") return Number.POSITIVE_INFINITY;
  if (raw === "-Infinity") return Number.NEGATIVE_INFINITY;
  try {
    const bytes = Uint8Array.from(atob(raw), (c) => c.charCodeAt(0));
    const view = new DataView(bytes.buffer);
    if (bytes.length === 8) return view.getFloat64(0, true); // little-endian
    if (bytes.length === 4) return view.getFloat32(0, true);
  } catch {
    // not base64 — fall through
  }
  return undefined;
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
