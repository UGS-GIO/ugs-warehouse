// @vitest-environment jsdom
import { describe, expect, it } from "vitest";

import { assetKind, cubeVariables, timeDimensionOf } from "./stac";
import { fillValueOf } from "./zarr/store";

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

describe("fillValueOf", () => {
  it("prefers the array's own sentinel", () => {
    expect(fillValueOf({ _FillValue: -32768 })).toBe(-32768);
  });

  it("accepts missing_value", () => {
    expect(fillValueOf({ missing_value: -1 })).toBe(-1);
  });

  // NaN would mask nothing on the GPU; -9999 is the export default.
  it("ignores a non-finite sentinel", () => {
    expect(fillValueOf({ _FillValue: NaN })).toBe(-9999);
  });
});
