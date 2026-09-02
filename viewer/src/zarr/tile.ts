/**
 * Per-tile GPU path, ported from the soil-water viewer: read the chunk the ZarrLayer asks for,
 * upload it as a single-channel float texture, and let the shader do nodata/rescale/colormap.
 */
import type { MinimalTileData, RenderTileResult } from "@developmentseed/deck.gl-raster";
import { Colormap, CreateTexture, FilterNoDataVal, LinearRescale } from "@developmentseed/deck.gl-raster/gpu-modules";
import type { GetTileDataOptions } from "@developmentseed/deck.gl-zarr";
import type { Texture } from "@luma.gl/core";
import * as zarr from "zarrita";

export type ZarrTileData = MinimalTileData & { texture: Texture };

/**
 * A nodata value the shader can actually match.
 *
 * Stores declare a non-finite fill (NaN) for nodata, but `FilterNoDataVal` compares by equality
 * and NaN never equals itself — drivers may also fold shader NaN checks to false. So NaN is
 * resolved on the CPU to this sentinel instead. Kept inside mediump range (max ~65504): a
 * float32-limit sentinel becomes Inf on mobile GPUs and stops comparing equal.
 */
export const NODATA_SENTINEL = -32768;

/** The value to hand `FilterNoDataVal`: the store's own fill when it is finite, else the
 * sentinel that `maskNaN` writes. */
export function effectiveNoData(fill: number): number {
  return Number.isFinite(fill) ? fill : NODATA_SENTINEL;
}

/** Replace NaN in place with the value the shader will test for, so nodata does not depend on
 * driver NaN handling. Must be passed the SAME value `makeRenderTile` filters on: writing a
 * fixed sentinel while the shader tests the store's own finite fill masks nothing. */
export function maskNaN(data: Float32Array, noDataValue: number): Float32Array {
  for (let i = 0; i < data.length; i++) {
    if (Number.isNaN(data[i])) data[i] = noDataValue;
  }
  return data;
}

/** `noDataValue` is the store's declared fill; the tile is masked to whatever
 * `effectiveNoData` resolves it to, which is what the shader filters. */
export function makeGetTileData(noDataValue: number) {
  const sentinel = effectiveNoData(noDataValue);
  return async function getTileData(
    arr: zarr.Array<zarr.NumberDataType, zarr.Readable>,
    options: GetTileDataOptions,
  ): Promise<ZarrTileData> {
    const { device, sliceSpec, width, height, signal } = options;
    const chunk = await zarr.get(arr, sliceSpec, { signal });
    if (chunk.shape.length !== 2) {
      throw new Error(`Expected a 2D (y, x) slice, got [${chunk.shape.join(", ")}]`);
    }
    // Cast, not a copy — openZarr rejects anything but float32, and this runs per tile.
    const data = maskNaN(chunk.data as Float32Array, sentinel);
    const texture = device.createTexture({
      format: "r32float",
      width,
      height,
      data,
      sampler: { minFilter: "nearest", magFilter: "nearest", addressModeU: "clamp-to-edge", addressModeV: "clamp-to-edge" },
    });
    return { texture, width, height, byteLength: data.byteLength };
  };
}

export function makeRenderTile(args: {
  colormapTexture: Texture;
  colormapIndex: number;
  noDataValue: number;
  rescaleMin: number;
  rescaleMax: number;
}) {
  const { colormapTexture, colormapIndex, noDataValue, rescaleMin, rescaleMax } = args;
  return (data: ZarrTileData): RenderTileResult => ({
    renderPipeline: [
      { module: CreateTexture, props: { textureName: data.texture } },
      { module: FilterNoDataVal, props: { value: effectiveNoData(noDataValue) } },
      { module: LinearRescale, props: { rescaleMin, rescaleMax } },
      { module: Colormap, props: { colormapTexture, colormapIndex, reversed: false } },
    ],
  });
}
