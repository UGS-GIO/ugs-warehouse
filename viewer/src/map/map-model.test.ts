import { describe, expect, it } from "vitest";
import { type ActiveLayer, boundsOf, clampSize, colorForId, DETENTS, hasFootprint, LAYER_COLORS, layerParam,
  mapKindOf, nearestDetent, releaseDetent, contentTakesDrag, nextPick, NO_LAYERS, orderedSublayerIds, parseLayerParam, reorderLayers, slugOf,
  validBbox } from "./map-model";
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

describe("releaseDetent", () => {
  it("snaps to the nearest detent on a slow release", () => {
    expect(releaseDetent(0.5, 0.2)).toBe(1);
  });

  it("goes one detent further on a flick, even a short one", () => {
    expect(releaseDetent(0.1, 2)).toBe(1);
    expect(releaseDetent(0.6, 2)).toBe(2);
    expect(releaseDetent(0.5, -2)).toBe(0);
    expect(releaseDetent(0.9, -2)).toBe(1);
    expect(releaseDetent(DETENTS[1], -2)).toBe(0);
    expect(releaseDetent(DETENTS[1], 2)).toBe(2);
  });

  it("stays at the end when a flick has nowhere further to go", () => {
    expect(releaseDetent(DETENTS[2], 2)).toBe(2);
    expect(releaseDetent(DETENTS[0], -2)).toBe(0);
  });
});

describe("contentTakesDrag", () => {
  it("drags down only from the top of the content", () => {
    expect(contentTakesDrag(0, 5, true, 2)).toBe(true);
    expect(contentTakesDrag(0, 5, false, 2)).toBe(false);
  });

  it("drags up only while the sheet can grow", () => {
    expect(contentTakesDrag(0, -5, false, 1)).toBe(true);
    expect(contentTakesDrag(0, -5, true, 2)).toBe(false);
  });

  it("leaves a sideways swipe alone", () => {
    expect(contentTakesDrag(6, 5, true, 1)).toBe(false);
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

describe("reorderLayers", () => {
  it("moves a layer down the draw order", () => {
    expect(reorderLayers(["a", "b", "c"], 0, 2)).toEqual(["b", "c", "a"]);
  });

  it("moves a layer up the draw order", () => {
    expect(reorderLayers(["a", "b", "c"], 2, 0)).toEqual(["c", "a", "b"]);
  });

  it("is a no-op when the layer doesn't move", () => {
    expect(reorderLayers(["a", "b", "c"], 1, 1)).toEqual(["a", "b", "c"]);
  });

  it("does not mutate the input array", () => {
    const ids = ["a", "b", "c"];
    reorderLayers(ids, 0, 2);
    expect(ids).toEqual(["a", "b", "c"]);
  });

  it("returns the order unchanged for an out-of-range index (a bad drag can't corrupt ?l=)", () => {
    expect(reorderLayers(["a", "b", "c"], -1, 1)).toEqual(["a", "b", "c"]);
    expect(reorderLayers(["a", "b", "c"], 1, 9)).toEqual(["a", "b", "c"]);
  });
});

describe("colorForId", () => {
  it("gives a layer the same color every time, regardless of active-set order (drag can't recolor it)", () => {
    expect(colorForId("hazards_qfaults")).toBe(colorForId("hazards_qfaults"));
  });

  it("only ever returns a palette color", () => {
    for (const id of ["a", "hazards_qfaults", "geolmap_500k", "wells_spatial"]) {
      expect(LAYER_COLORS).toContain(colorForId(id));
    }
  });

  it("spreads distinct ids across the palette rather than collapsing to one color", () => {
    const ids = ["a", "b", "c", "d", "hazards_qfaults", "geolmap_500k", "wells_spatial", "landslides"];
    expect(new Set(ids.map(colorForId)).size).toBeGreaterThan(1);
  });
});

describe("orderedSublayerIds", () => {
  const L = (id: string, extra: Partial<ActiveLayer> = {}): ActiveLayer => ({ id, title: id, ...extra });
  const styled = (counts: Record<string, number>) => (id: string) => counts[id];

  it("gives an unstyled vector layer its fill/line/circle fallback ids", () => {
    expect(orderedSublayerIds([L("qfaults")], { styledCount: () => undefined, cogReady: false }))
      .toEqual(["pm-qfaults-fill", "pm-qfaults-line", "pm-qfaults-circle"]);
  });

  it("gives a styled vector layer one id per resolved style layer, in order", () => {
    expect(orderedSublayerIds([L("qfaults")], { styledCount: styled({ qfaults: 3 }), cogReady: false }))
      .toEqual(["pm-qfaults-0", "pm-qfaults-1", "pm-qfaults-2"]);
  });

  it("renders no ids for a resolved-but-empty style (map.tsx draws nothing there — not the fallback)", () => {
    expect(orderedSublayerIds([L("qfaults")], { styledCount: styled({ qfaults: 0 }), cogReady: false }))
      .toEqual([]);
  });

  it("maps a raster PMTiles mosaic and a ready COG to their single raster id", () => {
    expect(orderedSublayerIds([L("geo", { rasterPmHref: "x" })], { styledCount: () => undefined, cogReady: false }))
      .toEqual(["rpm-geo-raster"]);
    expect(orderedSublayerIds([L("dem", { cogHref: "x" })], { styledCount: () => undefined, cogReady: true }))
      .toEqual(["cog-dem-raster"]);
  });

  it("omits a COG until its protocol is ready, and a zarr datacube always (the deck overlay draws it)", () => {
    expect(orderedSublayerIds([L("dem", { cogHref: "x" })], { styledCount: () => undefined, cogReady: false }))
      .toEqual([]);
    expect(orderedSublayerIds([L("cube", { zarr: { href: "x", variable: "v", pinDims: [] } })],
      { styledCount: () => undefined, cogReady: true })).toEqual([]);
  });

  it("flattens across layers in the given order, and slugs ids that aren't source-id-safe", () => {
    expect(orderedSublayerIds([L("a.b:c", { rasterPmHref: "x" }), L("qfaults")],
      { styledCount: () => undefined, cogReady: false }))
      .toEqual(["rpm-a_b_c-raster", "pm-qfaults-fill", "pm-qfaults-line", "pm-qfaults-circle"]);
    expect(slugOf("a.b:c")).toBe("a_b_c");
  });
});
