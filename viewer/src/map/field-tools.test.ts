import { describe, expect, it } from "vitest";
import { opacityParam, parseOpacityParam, withOpacity } from "./layer-opacity";
import { headingOf } from "./location";
import { formatCoords, pinOf, pointLink } from "./share-point";

describe("layer opacity in the URL", () => {
  it("round-trips, clamps, and leaves full opacity out", () => {
    expect(parseOpacityParam("geol~0.6,GQ-378~1.4,bad~x,~0.2")).toEqual({ geol: 0.6, "GQ-378": 1 });
    expect(opacityParam({ geol: 0.6, other: 1 })).toBe("geol~0.6");
    expect(opacityParam({ other: 1 })).toBeUndefined();
  });
});

describe("withOpacity", () => {
  it("scales a layer type's opacity, set or not", () => {
    expect(withOpacity({ "fill-color": "#f00", "fill-opacity": 0.8 }, "fill", 0.5)).toEqual({ "fill-color": "#f00", "fill-opacity": 0.4 });
    expect(withOpacity({}, "raster", 0.3)).toEqual({ "raster-opacity": 0.3 });
    expect(withOpacity({ "circle-color": "#000" }, "circle", 0.5)).toMatchObject({ "circle-opacity": 0.5, "circle-stroke-opacity": 0.5 });
  });

  it("keeps a zoom curve on top, scaling its outputs, and wraps other expressions", () => {
    const curve = ["interpolate", ["linear"], ["zoom"], 5, 0.2, 12, 0.8];
    expect(withOpacity({ "fill-opacity": curve }, "fill", 0.5)).toEqual({ "fill-opacity": ["interpolate", ["linear"], ["zoom"], 5, 0.1, 12, 0.4] });
    expect(withOpacity({ "line-opacity": ["step", ["zoom"], 0.5, 10, 1] }, "line", 0.5))
      .toEqual({ "line-opacity": ["step", ["zoom"], 0.25, 10, 0.5] });
    const data = ["get", "alpha"];
    expect(withOpacity({ "fill-opacity": data }, "fill", 0.5)).toEqual({ "fill-opacity": ["*", 0.5, data] });
  });

  it("changes nothing at full opacity", () => {
    expect(withOpacity({ "fill-opacity": 0.8 }, "fill", 1)).toEqual({ "fill-opacity": 0.8 });
    expect(withOpacity(undefined, "fill", 1)).toBeUndefined();
  });
});

describe("headingOf", () => {
  it("reads iOS's compass heading, else an absolute alpha turned clockwise", () => {
    expect(headingOf({ alpha: 10, absolute: false, webkitCompassHeading: 92 })).toBe(92);
    expect(headingOf({ alpha: 90, absolute: true })).toBe(270);
    expect(headingOf({ alpha: 0, absolute: true })).toBe(0);
    expect(headingOf({ alpha: 90, absolute: false })).toBeNull();   // relative to where the page loaded
    expect(headingOf({ alpha: null, absolute: true })).toBeNull();
  });
});

describe("share a point", () => {
  it("formats coordinates latitude first, to five places", () => {
    expect(formatCoords(40.5594, -111.7)).toBe("40.55940, -111.70000");
  });

  it("links to the point and zoom with a pin, keeping the rest of the page", () => {
    const link = pointLink("https://data.geology.utah.gov/map?l=geol&m=-111,39,6.00", -111.7, 40.5594, 13.4);
    const u = new URL(link);
    expect(u.searchParams.get("l")).toBe("geol");
    expect(u.searchParams.get("m")).toBe("-111.70000,40.55940,13.40");
    expect(pinOf(u.search)).toEqual({ lng: -111.7, lat: 40.5594 });
  });

  it("ignores a missing or bad pin", () => {
    expect(pinOf("")).toBeNull();
    expect(pinOf("?pin=,")).toBeNull();
    expect(pinOf("?pin=95,10")).toBeNull();
  });
});
