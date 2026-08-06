import { describe, expect, it } from "vitest";
import { layerCollectionIds, serviceUrlOf } from "./catalog";

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

describe("serviceUrlOf", () => {
  const item = (links: { rel: string; href: string }[]) => ({ links });

  it("reads the endpoint the catalog published", () => {
    expect(serviceUrlOf(item([
      { rel: "self", href: "./hazards_qfaults.json" },
      { rel: "service", href: "https://features.example/collections/hazards_qfaults" },
    ]))).toBe("https://features.example/collections/hazards_qfaults");
  });

  it("returns nothing for an item featureserv does not serve", () => {
    // Raster and publication items carry no service link — no row beats a URL built from the id,
    // which is what made those links 404 (#85).
    expect(serviceUrlOf(item([{ rel: "self", href: "./x.json" }]))).toBeUndefined();
    expect(serviceUrlOf(undefined)).toBeUndefined();
  });

  it("lets a session override swap the host", () => {
    expect(serviceUrlOf(item([{ rel: "service", href: "https://prod.example/collections/x" }]),
                        "http://localhost:9000")).toBe("http://localhost:9000/collections/x");
  });
});
