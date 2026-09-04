import { describe, expect, it } from "vitest";
import { boundsOf, clampSize, DETENTS, hasFootprint, layerParam, mapKindOf, nearestDetent, nextPick,
  NO_LAYERS, parseLayerParam, validBbox } from "./map-model";
import type { StacDoc } from "@/stac";

describe("validBbox", () => {
  it("returns undefined for missing / non-array / too-short bbox", () => {
    expect(validBbox(undefined)).toBeUndefined();
    expect(validBbox([] as number[])).toBeUndefined();
    expect(validBbox([1, 2, 3])).toBeUndefined();
  });

  it("passes a normal 4-length lon/lat bbox through unchanged", () => {
    expect(validBbox([-114, 37, -109, 42])).toEqual([-114, 37, -109, 42]);
  });

  it("drops the elevation ordinates from a 6-length 3D bbox", () => {
    // [w, s, minZ, e, n, maxZ] -> [w, s, e, n]
    expect(validBbox([-114, 37, 1200, -109, 42, 4000])).toEqual([-114, 37, -109, 42]);
  });

  it("accepts exact boundary values (±180 lon, ±90 lat)", () => {
    expect(validBbox([-180, -90, 180, 90])).toEqual([-180, -90, 180, 90]);
  });

  it("rejects a projected-metres bbox (UTM) — every ordinate out of range", () => {
    // enmin_geophysics_heatflow's real bbox: EPSG:326xx metres, never reprojected.
    expect(validBbox([232162.4, 4097922.6, 671192.0, 4651333.4])).toBeUndefined();
  });

  it("rejects an out-of-range latitude (a longitude leaked into the lat slot)", () => {
    // wells_spatial's real bbox: south = -110.29 is an impossible latitude.
    expect(validBbox([-145.16, -110.29, 40.42, 44.49])).toBeUndefined();
  });

  it("rejects an out-of-range longitude", () => {
    expect(validBbox([-190, 37, -109, 42])).toBeUndefined();
    expect(validBbox([-114, 37, 181, 42])).toBeUndefined();
  });

  it("rejects NaN / Infinity ordinates", () => {
    expect(validBbox([NaN, 37, -109, 42])).toBeUndefined();
    expect(validBbox([-114, 37, Infinity, 42])).toBeUndefined();
  });
});

describe("boundsOf", () => {
  it("returns [[sw],[ne]] corners for a valid bbox", () => {
    const item = { bbox: [-114, 37, -109, 42] } as StacDoc;
    expect(boundsOf(item)).toEqual([[-114, 37], [-109, 42]]);
  });

  it("returns undefined for a malformed bbox (no throw downstream)", () => {
    expect(boundsOf({ bbox: [232162, 4097922, 671192, 4651333] } as StacDoc)).toBeUndefined();
    expect(boundsOf(undefined)).toBeUndefined();
  });
});

describe("mapKindOf", () => {
  it("is 'none' when no surface is available", () => {
    expect(mapKindOf({})).toBe("none");
  });

  it("follows the priority vector > raster > cog > footprint", () => {
    expect(mapKindOf({ vector: true, footprint: true })).toBe("vector");
    expect(mapKindOf({ raster: true, cog: true, footprint: true })).toBe("raster");
    expect(mapKindOf({ cog: true, footprint: true })).toBe("cog");
    expect(mapKindOf({ footprint: true })).toBe("footprint");
  });
});

describe("hasFootprint", () => {
  it("is true for geometry or a real (>=4) bbox", () => {
    expect(hasFootprint({ geometry: { type: "Point", coordinates: [-111, 39] } } as StacDoc)).toBe(true);
    expect(hasFootprint({ bbox: [-114, 37, -109, 42] } as StacDoc)).toBe(true);
  });

  it("is false for aspatial items / too-short bbox / undefined", () => {
    expect(hasFootprint(undefined)).toBe(false);
    expect(hasFootprint({} as StacDoc)).toBe(false);
    expect(hasFootprint({ bbox: [1, 2] } as StacDoc)).toBe(false);
  });
});

describe("nextPick", () => {
  it("starts the nonce at 1 from a null previous pick", () => {
    expect(nextPick(null, 42)).toEqual({ id: 42, nonce: 1 });
  });

  it("bumps the nonce so re-clicking the SAME id still changes the value", () => {
    const first = nextPick(null, 42);
    const second = nextPick(first, 42);
    expect(second).toEqual({ id: 42, nonce: 2 });
    expect(second).not.toEqual(first); // downstream effect keyed on nonce re-fires
  });

  it("carries the new id and bumps the nonce when the feature changes", () => {
    expect(nextPick({ id: 42, nonce: 3 }, 99)).toEqual({ id: 99, nonce: 4 });
  });
});

describe("nearestDetent", () => {
  it("snaps to the closest detent", () => {
    expect(nearestDetent(0)).toBe(0);
    expect(nearestDetent(0.2)).toBe(0);
    expect(nearestDetent(0.5)).toBe(1);
    expect(nearestDetent(1)).toBe(2);
  });

  it("breaks a tie toward the lower detent, which shows more map", () => {
    const mid = (DETENTS[0] + DETENTS[1]) / 2;
    expect(nearestDetent(mid)).toBe(0);
  });
});

describe("clampSize", () => {
  it("clamps into range", () => {
    expect(clampSize(10, 100, 500, 200)).toBe(100);
    expect(clampSize(900, 100, 500, 200)).toBe(500);
    expect(clampSize(300, 100, 500, 200)).toBe(300);
  });

  it("falls back rather than collapsing a pane on a non-numeric stored value", () => {
    expect(clampSize(NaN, 100, 500, 200)).toBe(200);
  });
});

describe("the `l` layer param", () => {
  it("keeps 'nothing chosen yet' and 'everything off' apart", () => {
    // Collapsing these is the bug: with an item open, "off" fell back to drawing that item.
    expect(parseLayerParam(undefined)).toBeUndefined();
    expect(parseLayerParam(NO_LAYERS)).toEqual([]);
  });

  it("round-trips a selection", () => {
    expect(parseLayerParam(layerParam(["a", "b"]))).toEqual(["a", "b"]);
    expect(parseLayerParam(layerParam([]))).toEqual([]);
    expect(parseLayerParam(layerParam(undefined))).toBeUndefined();
  });

  it("writes a value for 'off', since the router drops an empty param", () => {
    expect(layerParam([])).toBe(NO_LAYERS);
    expect(layerParam(undefined)).toBeUndefined();
  });

  it("ignores empty segments from a hand-edited url", () => {
    expect(parseLayerParam("a,,b,")).toEqual(["a", "b"]);
  });
});
