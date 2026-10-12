import { describe, expect, it } from "vitest";
import { catalogTrail, featureEndpoints, itemBackTarget, layerCollectionIds, layerRowsOf, rootGroupOf, serviceUrlOf } from "./catalog";
import type { StacDoc } from "@/stac";

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

describe("catalogTrail", () => {
  const pubs = { id: "ugs-publications", title: "Publications" };
  const ofr = { id: "ugs-publications/OFR", title: "Open File Report", parentId: "ugs-publications" };

  it("walks an Item inside a publication through its series and publication", () => {
    const trail = catalogTrail({
      collectionId: "ugs-publications/OFR/OFR-9", itemUrl: "OFR-9_geounits", subCats: [pubs], leaf: ofr,
      pub: { sid: "OFR-9", title: "Geologic map of X" },
    });
    expect(trail).toEqual([
      { label: "Catalog", to: {} },
      { label: "Publications", to: { c: "ugs-publications" } },
      { label: "Open File Report", to: { c: "ugs-publications/OFR", keepSeries: true } },
      { label: "Geologic map of X", to: { c: "ugs-publications/OFR", i: "OFR-9" } },
      { label: "OFR-9_geounits" },
    ]);
  });

  it("names the publication by its SID until its title loads", () => {
    const trail = catalogTrail({ collectionId: "ugs-publications/OFR/OFR-9", itemUrl: "x", subCats: [pubs],
      leaf: ofr, pub: { sid: "OFR-9" } });
    expect(trail[3]).toEqual({ label: "OFR-9", to: { c: "ugs-publications/OFR", i: "OFR-9" } });
  });

  it("keeps the old trail for a collection and a sub-catalog", () => {
    expect(catalogTrail({ collectionId: "ugs-publications/OFR", subCats: [pubs], leaf: ofr })).toEqual([
      { label: "Catalog", to: {} },
      { label: "Publications", to: { c: "ugs-publications" } },
      { label: "Open File Report", to: undefined },
    ]);
    expect(catalogTrail({ collectionId: "ugs-publications", subCats: [pubs], subCat: pubs })).toEqual([
      { label: "Catalog", to: {} }, { label: "Publications" },
    ]);
    expect(catalogTrail({ subCats: [] })).toEqual([{ label: "Catalog", to: undefined }]);
  });
});

describe("catalogTrail titles in a publication", () => {
  const pubs = { id: "ugs-publications", title: "Publications" };
  const m = { id: "ugs-publications/M", title: "Map", parentId: "ugs-publications" };
  it("labels a publication page by series and publication title", () => {
    expect(catalogTrail({ collectionId: m.id, itemUrl: "M-246DM", subCats: [pubs], leaf: m,
      itemTitle: "Landslide maps of Utah" }).map((c) => c.label))
      .toEqual(["Catalog", "Publications", "Map", "Landslide maps of Utah"]);
  });
  it("labels a layer page by publication and layer title", () => {
    expect(catalogTrail({ collectionId: `${m.id}/M-246DM`, itemUrl: "M-246DM_UtahLandslideMaps__DebrisFlowPaths",
      subCats: [pubs], leaf: m, pub: { sid: "M-246DM", title: "Landslide maps of Utah" },
      itemTitle: "Debris Flow Paths" }).map((c) => c.label))
      .toEqual(["Catalog", "Publications", "Map", "Landslide maps of Utah", "Debris Flow Paths"]);
  });
  it("falls back to the item id until the title loads", () => {
    expect(catalogTrail({ collectionId: m.id, itemUrl: "M-246DM", subCats: [pubs], leaf: m, itemTitle: "" })
      .at(-1)?.label).toBe("M-246DM");
  });
});

describe("featureEndpoints", () => {
  const service = "https://features.example/collections/hazards_qfaults";
  it("offers the features service only for an item with its own rel=service link", () => {
    expect(featureEndpoints({ links: [{ rel: "service", href: service }] }).map((r) => r.url))
      .toEqual([service, `${service}/items?limit=50`]);
  });
  it("builds no URL for a publication layer, which has a GeoParquet but no service link", () => {
    expect(featureEndpoints({ links: [{ rel: "collection", href: "../collection.json" }] })).toEqual([]);
    expect(featureEndpoints(undefined)).toEqual([]);
  });
  it("points the link at an overriding base host", () => {
    expect(featureEndpoints({ links: [{ rel: "service", href: service }] }, "http://localhost:9000")[0].url)
      .toBe("http://localhost:9000/collections/hazards_qfaults");
  });
});

describe("itemBackTarget", () => {
  it("returns from an Item inside a publication to the publication", () => {
    expect(itemBackTarget({
      partOf: { c: "ugs-publications/OFR", i: "OFR-9" }, collectionUrl: "ugs-publications/OFR/OFR-9", seriesSel: ["x"],
    })).toEqual({ c: "ugs-publications/OFR", i: "OFR-9" });
  });
  it("returns from any other item to its collection's list, keeping the series filter", () => {
    expect(itemBackTarget({ collectionUrl: "ugs-publications/B", seriesSel: ["B"] }))
      .toEqual({ c: "ugs-publications/B", s: ["B"] });
  });
});

describe("layerRowsOf", () => {
  const draws = (d?: StacDoc) => Boolean(d?.assets?.cog);
  const cog = { cog: { href: "https://x/a.tif" } };
  const items = [
    { collId: "ugs-serving-topics/hazards", href: "https://x/ugs-serving-topics/hazards/qf/qf.json",
      data: { id: "qf", properties: { title: "Faults" }, assets: cog } },
    // A publication's layer, tagged in the root index; its collection is the publication.
    { collId: "ugs-publications/OFR/OFR-9", href: "https://x/ugs-publications/OFR/OFR-9/OFR-9_flows/OFR-9_flows.json",
      data: { id: "OFR-9_flows", properties: { title: "Debris Flow Paths", "ugs:in_publication": "OFR-9",
                                                "ugs:publication_title": "Geologic map of X" }, assets: cog } },
    { collId: "ugs-publications/OFR", href: "https://x/ugs-publications/OFR/OFR-2/collection.json",
      data: { id: "OFR-2", properties: { title: "Report" } } },
  ];
  const titles: Record<string, string> = { "ugs-serving-topics/hazards": "Hazards", "ugs-publications/OFR": "Open File Report" };

  it("keeps a publication's layer, filed under its series and named with its publication", () => {
    const rows = layerRowsOf(items, {
      draws, collTitle: (id) => titles[id] ?? id, isLayer: (r) => r.collId.startsWith("ugs-serving-topics/"),
    });
    expect(rows).toEqual([
      { id: "qf", href: items[0].href, title: "Faults", group: "Hazards", layer: true },
      { id: "OFR-9_flows", href: items[1].href, title: "Debris Flow Paths · Geologic map of X",
        group: "Open File Report", layer: false },
    ]);
  });
});
