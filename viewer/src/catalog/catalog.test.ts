import { describe, expect, it } from "vitest";
import { layerCollectionIds, rootGroupOf, serviceUrlOf } from "./catalog";

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

describe("rootGroupOf", () => {
  it("separates layers from documents so the landing can group them", () => {
    expect(rootGroupOf("ugs-serving-topics")).toBe("layers");
    expect(rootGroupOf("ugs-rasters")).toBe("layers");
    expect(rootGroupOf("ugs-geologic-maps")).toBe("layers");
    expect(rootGroupOf("ugs-publications")).toBe("documents");
    expect(rootGroupOf("ugs-mining-district-files")).toBe("documents");
  });

  it("never guesses for a federated catalog — it lives elsewhere and can hold anything", () => {
    expect(rootGroupOf("https://ubm-assets.geology.utah.gov/stac/catalog.json")).toBe("federated");
  });

  // The alarm for a new root (ugs-flux): classify it here rather than let it read as a publication.
  it("says other for a root child it doesn't know", () => {
    expect(rootGroupOf("ugs-flux")).toBe("other");
  });

  it("classifies every root child the live catalog publishes", () => {
    const live = ["ugs-external", "ugs-geologic-maps", "ugs-mining-district-files",
      "ugs-publications", "ugs-rasters", "ugs-serving-topics"];
    for (const id of live) expect(rootGroupOf(id), id).not.toBe("other");
  });
});
