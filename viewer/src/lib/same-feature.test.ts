import { describe, expect, it } from "vitest";
import { sameFeature } from "./same-feature";

describe("sameFeature", () => {
  it("matches a tile feature to its row, whatever the number types", () => {
    expect(sameFeature({ name: "Fan 12", area: 1.5, n: 7 }, { name: "Fan 12", area: 1.5000000001, n: "7", feature_id: 3 })).toBe(true);
  });
  it("skips dates, which the tile and the table write differently", () => {
    expect(sameFeature({ d: "2026-09-23", name: "A" }, { d: "2026-09-23T00:00:00.000Z", name: "A" })).toBe(true);
  });
  it("catches the same id on another record", () => {
    expect(sameFeature({ name: "Fan 12", area: 1.5 }, { name: "Fan 99", area: 1.5 })).toBe(false);
  });
  it("ignores the id, the geometry and the bbox columns, and values one side lacks", () => {
    expect(sameFeature({ feature_id: 1, bbox_xmin: 0, extra: "x" }, { feature_id: 2, bbox_xmin: 9 })).toBe(true);
  });
});
