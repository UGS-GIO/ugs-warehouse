// @vitest-environment jsdom
import { describe, expect, it } from "vitest";

import { assetKind, cubeSteps, cubeVariables, nonSpatialDimensions, resolveSelection, stepKey, timeDimensionOf } from "@/stac";
import { decodeFillValue, fillValueOf } from "./store";
import { effectiveNoData, maskNaN, NODATA_SENTINEL } from "./tile";
import { cubeParam, parseCubeParam } from "./cube-picks";
import { stepFor } from "./steps";
import { describeZarrError } from "./use-zarr-layers";

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

describe("cubeSteps", () => {
  const cube = (dim: string, d: Record<string, unknown>) => ({ properties: { "cube:dimensions": { [dim]: d } } });

  it("enumerates a monthly series from its extent, as year + month", () => {
    const steps = cubeSteps(cube("time", { type: "temporal", extent: ["2005-01-01T00:00:00Z", "2025-12-01T00:00:00Z"], step: "P1M" }), "time");
    expect(steps).toHaveLength(252);   // DAYMET_DISALEXI's time axis
    expect(steps[0]).toEqual({ label: "Jan 2005", year: 2005, month: 1 });
    expect(steps.at(-1)).toEqual({ label: "Dec 2025", year: 2025, month: 12 });
  });

  it("reads the climatology month dim as months only", () => {
    const steps = cubeSteps(cube("month", { type: "other", extent: [1, 12], unit: "month" }), "month");
    expect(steps.map((s) => s.label)).toEqual(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]);
    expect(steps[0].year).toBeUndefined();
  });

  it("prefers listed values over the extent", () => {
    const steps = cubeSteps(cube("time", { type: "temporal", values: ["2020-06-01", "2021-06-01"], extent: ["2020-01-01", "2021-12-01"], step: "P1M" }), "time");
    expect(steps.map((s) => s.label)).toEqual(["Jun 2020", "Jun 2021"]);
  });

  it("resolves a daily series to year + month + day", () => {
    const steps = cubeSteps(cube("time", { type: "temporal", extent: ["2020-02-27", "2020-03-01"], step: "P1D" }), "time");
    expect(steps.map((s) => s.label)).toEqual(["Feb 27, 2020", "Feb 28, 2020", "Feb 29, 2020", "Mar 1, 2020"]);
    expect(steps[3]).toMatchObject({ year: 2020, month: 3, day: 1 });
  });

  it("resolves a yearly series to year only", () => {
    const steps = cubeSteps(cube("time", { type: "temporal", extent: ["2000-01-01", "2002-01-01"], step: "P1Y" }), "time");
    expect(steps).toEqual([{ label: "2000", year: 2000 }, { label: "2001", year: 2001 }, { label: "2002", year: 2002 }]);
  });

  it("infers the resolution of listed dates", () => {
    const yearly = cubeSteps(cube("time", { values: ["2001-01-01", "2002-01-01"] }), "time");
    expect(yearly[0]).toEqual({ label: "2001", year: 2001 });
    const daily = cubeSteps(cube("time", { values: ["2001-01-01", "2001-01-02"] }), "time");
    expect(daily[1].day).toBe(2);
  });

  it("can't enumerate a sub-daily step without values", () => {
    expect(cubeSteps(cube("time", { type: "temporal", extent: ["2020-01-01", "2020-01-02"], step: "PT1H" }), "time")).toEqual([]);
  });
});

describe("stepFor", () => {
  // 2010 has Jan–Dec; 2011 only Jan–Mar (a series that ends early).
  const steps = cubeSteps({ properties: { "cube:dimensions": { time: {
    type: "temporal", extent: ["2010-01-01", "2011-03-01"], step: "P1M" } } } }, "time");
  const at = (label: string) => steps.findIndex((s) => s.label === label);

  it("keeps the month when the new year has it", () => {
    expect(steps[stepFor(steps, steps[at("Feb 2010")], "year", 2011)].label).toBe("Feb 2011");
  });

  it("falls back to the nearest month the new year has", () => {
    expect(steps[stepFor(steps, steps[at("Oct 2010")], "year", 2011)].label).toBe("Mar 2011");
  });

  it("changes the month within the current year", () => {
    expect(steps[stepFor(steps, steps[at("Feb 2011")], "month", 1)].label).toBe("Jan 2011");
  });
});

describe("resolveSelection", () => {
  const monthly = [{ label: "Jan 2005", year: 2005, month: 1 }, { label: "Feb 2005", year: 2005, month: 2 }];
  const months = [{ label: "Jan", month: 1 }, { label: "Feb", month: 2 }];

  it("defaults a dated series to its latest step and a month dim to January", () => {
    expect(resolveSelection({ time: monthly, month: months })).toEqual({ time: 1, month: 0 });
  });

  it("finds a pick by its key", () => {
    expect(resolveSelection({ time: monthly, month: months }, { time: "2005-01", month: "2" })).toEqual({ time: 0, month: 1 });
  });

  // A step the store doesn't hold (STAC ran ahead of the data, or a stale link) falls back.
  it("falls back from a key it can't find", () => {
    expect(resolveSelection({ time: monthly }, { time: "2030-01" })).toEqual({ time: 1 });
  });

  it("pins a dim STAC can't enumerate to its first index", () => {
    expect(resolveSelection({ band: [] })).toEqual({ band: 0 });
  });
});

describe("stepKey", () => {
  it("keys a dated step by its date, at the series' resolution", () => {
    expect(stepKey({ label: "2010", year: 2010 }, 5)).toBe("2010");
    expect(stepKey({ label: "Jul 2010", year: 2010, month: 7 }, 5)).toBe("2010-07");
    expect(stepKey({ label: "Jul 3, 2010", year: 2010, month: 7, day: 3 }, 5)).toBe("2010-07-03");
  });

  it("keys a month dim by month and anything else by index", () => {
    expect(stepKey({ label: "Jul", month: 7 }, 6)).toBe("7");
    expect(stepKey({ label: "band 2" }, 1)).toBe("1");
  });
});

describe("cube param", () => {
  it("round-trips several cubes", () => {
    const all = { DAYMET_DISALEXI: { var: "AET", time: "2010-07" }, CLIM: { month: "7" } };
    expect(cubeParam(all)).toBe("DAYMET_DISALEXI~var=AET~time=2010-07,CLIM~month=7");
    expect(parseCubeParam(cubeParam(all))).toEqual(all);
  });

  it("drops a cube with no picks and ignores junk", () => {
    expect(cubeParam({ A: {} })).toBeUndefined();
    expect(parseCubeParam("A~bad~time=2010,~x=1")).toEqual({ A: { time: "2010" } });
    expect(parseCubeParam(undefined)).toEqual({});
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

describe("describeZarrError", () => {
  // A catalog item whose store was never published (the UBM Ensemble cubes, 2026-09) fails with a
  // 404 on its Icechunk entry point; that should read as "not published", not as a crash.
  it("reads a missing store as not published", () => {
    expect(describeZarrError(new Error("HTTP 404 fetching repo"))).toBe("No data is published for this datacube yet.");
    expect(describeZarrError(new Error("NoSuchKey"))).toBe("No data is published for this datacube yet.");
  });
  it("passes any other failure through", () => {
    expect(describeZarrError(new Error("Variable 'AET' is int16; only float32 renders today.")))
      .toBe("Could not open the datacube: Variable 'AET' is int16; only float32 renders today.");
  });
});
