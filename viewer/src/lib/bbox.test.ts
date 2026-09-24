import { describe, expect, it } from "vitest";
import { contains, overlaps } from "./bbox";

describe("bbox", () => {
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
