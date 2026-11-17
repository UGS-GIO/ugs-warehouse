import { describe, expect, it, vi } from "vitest";
import type { MapLayerMouseEvent } from "react-map-gl/maplibre";

import { pickFeatures } from "./highlight";

// A click on a map that has only the `mounted` layers, returning `under` at the point and `near` in a box.
const clickOn = (mounted: string[], under: unknown[], near: unknown[] = []) => {
  const queryRenderedFeatures = vi.fn((g: unknown) => (Array.isArray(g) && Array.isArray(g[0]) ? near : under));
  const e = { point: { x: 5, y: 5 }, target: { getLayer: (id: string) => (mounted.includes(id) ? {} : undefined), queryRenderedFeatures } };
  return { e: e as unknown as MapLayerMouseEvent, queryRenderedFeatures };
};

describe("pickFeatures", () => {
  it("asks only the layers already on the map, so one still loading does not blank the rest", () => {
    const { e, queryRenderedFeatures } = clickOn(["a"], [{ id: 1 }]);
    expect(pickFeatures(e, ["a", "b"])).toEqual([{ id: 1 }]);
    expect(queryRenderedFeatures).toHaveBeenCalledWith({ x: 5, y: 5 }, { layers: ["a"] });
  });

  it("looks in a box around the point when nothing is right under it", () => {
    vi.stubGlobal("matchMedia", () => ({ matches: false }));
    const { e } = clickOn(["a"], [], [{ id: 2 }]);
    expect(pickFeatures(e, ["a"])).toEqual([{ id: 2 }]);
    vi.unstubAllGlobals();
  });

  it("finds nothing when none of the layers is on the map", () => {
    expect(pickFeatures(clickOn([], [{ id: 1 }]).e, ["a"])).toEqual([]);
  });
});
