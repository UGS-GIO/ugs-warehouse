import { describe, expect, it } from "vitest";
import { contains, overlaps, to2d } from "./bbox";

describe("bbox", () => {
  it("reads the max corner of a 3D bbox after its heights", () => {
    expect(to2d([-112, 40, -111, 41])).toEqual([-112, 40, -111, 41]);
    expect(to2d([-112, 40, 1200, -111, 41, 1500])).toEqual([-112, 40, -111, 41]);
    expect(to2d([1, 2, 3])).toBeUndefined();
  });
  it("overlaps boxes that share area or an edge, not ones apart", () => {
    expect(overlaps([0, 0, 2, 2], [1, 1, 3, 3])).toBe(true);
    expect(overlaps([0, 0, 1, 1], [1, 0, 2, 1])).toBe(true);
    expect(overlaps([0, 0, 1, 1], [2, 2, 3, 3])).toBe(false);
  });
  it("contains points inside or on the edge", () => {
    expect(contains([0, 0, 1, 1], 0.5, 0.5)).toBe(true);
    expect(contains([0, 0, 1, 1], 1, 1)).toBe(true);
    expect(contains([0, 0, 1, 1], 1.1, 0.5)).toBe(false);
  });
});
