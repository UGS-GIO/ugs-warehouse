// @vitest-environment jsdom
import { describe, expect, it } from "vitest";

import { assetKind, cubeVariables, nonSpatialDimensions, timeDimensionOf } from "../stac";
import { decodeFillValue, fillValueOf } from "./store";
import { effectiveNoData, maskNaN, NODATA_SENTINEL } from "./tile";

// Shaped like the UBM items the warehouse federates.
const zarrAsset = { href: "https://ubm-assets.geology.utah.gov/zarr/ubm/v1/DAYMET_DISALEXI", type: "application/vnd.zarr", roles: ["data"] };

describe("assetKind", () => {
  it("recognises a zarr store by media type — the href has no extension", () => {
    expect(assetKind(zarrAsset)).toBe("zarr");
  });

  it("still recognises a .zarr suffix", () => {
    expect(assetKind({ href: "https://x/openet_test.zarr" })).toBe("zarr");
  });

  // Regression: zarr is tested before COG, so a datacube can't be mistaken for a GeoTIFF.
  it("leaves COGs alone", () => {
    expect(assetKind({ href: "https://x/a.cog.tif", type: "image/tiff; application=geotiff; profile=cloud-optimized" })).toBe("cog");
  });
});

describe("cubeVariables", () => {
  it("reads variables off properties", () => {
    const item = { properties: { "cube:variables": { AET: { type: "data" }, Recharge: { type: "data" } } } };
    expect(Object.keys(cubeVariables(item))).toEqual(["AET", "Recharge"]);
  });

  it("drops coordinate arrays — they aren't drawable", () => {
    const item = { properties: { "cube:variables": { AET: { type: "data" }, x: { type: "auxiliary" } } } };
    expect(Object.keys(cubeVariables(item))).toEqual(["AET"]);
  });

  // A producer that omits `type` shouldn't render an empty variable list.
  it("keeps untyped entries rather than filtering everything out", () => {
    const item = { properties: { "cube:variables": { AET: {} } } };
    expect(Object.keys(cubeVariables(item))).toEqual(["AET"]);
  });
});

describe("timeDimensionOf", () => {
  it("finds the temporal dim by type, whatever it's called", () => {
    const item = { properties: { "cube:dimensions": { t: { type: "temporal" }, x: { type: "spatial" } } } };
    expect(timeDimensionOf(item)).toBe("t");
  });

  it("falls back to the conventional name", () => {
    const item = { properties: { "cube:dimensions": { time: {}, x: { type: "spatial" } } } };
    expect(timeDimensionOf(item)).toBe("time");
  });

  it("returns undefined for a purely spatial cube", () => {
    const item = { properties: { "cube:dimensions": { x: { type: "spatial" }, y: { type: "spatial" } } } };
    expect(timeDimensionOf(item)).toBeUndefined();
  });
});

// ZarrLayer throws "selection is missing non-spatial dim" unless every one of these is pinned.
describe("nonSpatialDimensions", () => {
  it("pins a temporal dim", () => {
    const item = { properties: { "cube:dimensions": { time: { type: "temporal" }, x: { type: "spatial" }, y: { type: "spatial" } } } };
    expect(nonSpatialDimensions(item)).toEqual(["time"]);
  });

  // The bug this closes: the climatology cubes key on `month` (type "other"), so pinning only the
  // temporal dim left it unpinned and the layer threw on init.
  it("pins a non-temporal extra dim like month", () => {
    const item = { properties: { "cube:dimensions": { month: { type: "other" }, x: { type: "spatial" }, y: { type: "spatial" } } } };
    expect(nonSpatialDimensions(item)).toEqual(["month"]);
  });

  it("never pins the spatial axes, even untyped", () => {
    const item = { properties: { "cube:dimensions": { lat: {}, lon: {}, time: { type: "temporal" } } } };
    expect(nonSpatialDimensions(item)).toEqual(["time"]);
  });

  it("returns nothing for a purely 2D cube", () => {
    const item = { properties: { "cube:dimensions": { x: { type: "spatial" }, y: { type: "spatial" } } } };
    expect(nonSpatialDimensions(item)).toEqual([]);
  });
});

describe("fillValueOf", () => {
  it("prefers the array's own sentinel", () => {
    expect(fillValueOf({ _FillValue: -32768 })).toBe(-32768);
  });

  it("accepts missing_value", () => {
    expect(fillValueOf({ missing_value: -1 })).toBe(-1);
  });

  // Was: coerced to -9999 because "NaN would mask nothing on the GPU". But -9999 appears
  // nowhere in the data either, so that masked nothing too — nodata rendered as the colormap
  // floor. Keep what the array declares; the render boundary resolves it (effectiveNoData +
  // maskNaN), so the sentinel is decided where the shader constraint actually lives.
  it("keeps a non-finite sentinel rather than substituting one that matches nothing", () => {
    expect(fillValueOf({ _FillValue: NaN })).toBeNaN();
  });
});

describe("fill value decoding", () => {
  it("decodes a base64 IEEE NaN, which stores use because JSON has no NaN", () => {
    // Read as an opaque string this fell through to -9999, a value that appears nowhere in
    // the data, so nodata was never masked and rendered as the colormap floor.
    expect(decodeFillValue("AAAAAAAA+H8=")).toBeNaN();
    expect(fillValueOf({ _FillValue: "AAAAAAAA+H8=" })).toBeNaN();
  });

  it("decodes the spelled-out non-finite forms", () => {
    expect(decodeFillValue("NaN")).toBeNaN();
    expect(decodeFillValue("Infinity")).toBe(Number.POSITIVE_INFINITY);
    expect(decodeFillValue("-Infinity")).toBe(Number.NEGATIVE_INFINITY);
  });

  it("passes a plain numeric fill through", () => {
    expect(fillValueOf({ _FillValue: -9999 })).toBe(-9999);
    expect(fillValueOf({ missing_value: 0 })).toBe(0);
  });

  it("falls back only when the array declares nothing usable", () => {
    expect(fillValueOf({})).toBe(-9999);
    expect(decodeFillValue(undefined)).toBeUndefined();
  });
});

describe("nodata sentinel", () => {
  it("swaps a non-finite fill for one the shader can compare", () => {
    // FilterNoDataVal tests equality; NaN never equals itself.
    expect(effectiveNoData(Number.NaN)).toBe(NODATA_SENTINEL);
    expect(effectiveNoData(Number.POSITIVE_INFINITY)).toBe(NODATA_SENTINEL);
    expect(effectiveNoData(-9999)).toBe(-9999);
  });

  it("stays inside mediump range so mobile GPUs do not fold it to Inf", () => {
    expect(Math.abs(NODATA_SENTINEL)).toBeLessThan(65504);
  });

  it("masks NaN in place", () => {
    expect(Array.from(maskNaN(new Float32Array([1, Number.NaN, 3]), NODATA_SENTINEL)))
      .toEqual([1, NODATA_SENTINEL, 3]);
    // Must write what the shader tests for, not a fixed sentinel.
    expect(Array.from(maskNaN(new Float32Array([1, Number.NaN]), -9999))).toEqual([1, -9999]);
  });
});
