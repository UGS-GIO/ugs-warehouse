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

export async function getTileData(
  arr: zarr.Array<zarr.NumberDataType, zarr.Readable>,
  options: GetTileDataOptions,
): Promise<ZarrTileData> {
  const { device, sliceSpec, width, height, signal } = options;
  const chunk = await zarr.get(arr, sliceSpec, { signal });
  if (chunk.shape.length !== 2) {
    throw new Error(`Expected a 2D (y, x) slice, got [${chunk.shape.join(", ")}]`);
  }
  const data = Float32Array.from(chunk.data as ArrayLike<number>);
  const texture = device.createTexture({
    format: "r32float",
    width,
    height,
    data,
    sampler: { minFilter: "nearest", magFilter: "nearest", addressModeU: "clamp-to-edge", addressModeV: "clamp-to-edge" },
  });
  return { texture, width, height, byteLength: data.byteLength };
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
      { module: FilterNoDataVal, props: { value: noDataValue } },
      { module: LinearRescale, props: { rescaleMin, rescaleMax } },
      { module: Colormap, props: { colormapTexture, colormapIndex, reversed: false } },
    ],
  });
}
