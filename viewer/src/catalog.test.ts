import { describe, expect, it } from "vitest";
import { layerCollectionIds } from "./catalog";

describe("layerCollectionIds", () => {
  it("selects root leaf collections, not sub-catalogs", () => {
    const roots = [
      { id: "ugs-external", kind: "catalog" },
      { id: "ugs-publications", kind: "catalog" },
      { id: "ugs-serving-topics", kind: "collection" },
      { id: "ugs-geologic-maps", kind: "collection" },
    ];
    expect(layerCollectionIds(roots)).toEqual(["ugs-serving-topics", "ugs-geologic-maps"]);
  });
  it("auto-includes a future published layer collection like rasters", () => {
    const roots = [
      { id: "ugs-publications", kind: "catalog" },
      { id: "ugs-rasters", kind: "collection" },
    ];
    expect(layerCollectionIds(roots)).toEqual(["ugs-rasters"]);
  });
  it("includes serving-topic schema collections nested under their sub-catalog", () => {
    const roots = [
      { id: "ugs-publications", kind: "catalog" },
      { id: "ugs-serving-topics", kind: "catalog" },
    ];
    const nested = [
      { id: "ugs-serving-topics/hazards", parentId: "ugs-serving-topics" },
      { id: "ugs-serving-topics/emp", parentId: "ugs-serving-topics" },
      { id: "ugs-publications/DS", parentId: "ugs-publications" },  // pubs stay out
    ];
    expect(layerCollectionIds(roots, nested)).toEqual([
      "ugs-serving-topics/hazards", "ugs-serving-topics/emp"]);
  });
});
