// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";

import { assetUsages, catalogItemHref, citeLink, collectionAsItem, collectionLink, collectionParts, entryCollKeyOf, hasFootprint, HttpError, idOf, indexItemRefs, isCatalogHref, isCollectionHref, isPlaceholderFootprint, itemMissing, NoIndexError, resolutionStatus, layerLabel, pubHrefFor, pubOfEntry, pubSidIn, ROOT_INDEX_URL, rootIndexItems, cogAsset, cogRenderAsset, collKeyOf, isDrawableCog, hasItemsIndex, rasterTilesAsset, relatedJoins, viaLink } from "./stac";
import type { StacDoc } from "./stac";

const OURS = "https://maps-assets.geology.utah.gov/warehouse/stac/catalog.json";

describe("hasItemsIndex", () => {
  it("asks for the rollup on our own collections", () => {
    expect(hasItemsIndex("https://maps-assets.geology.utah.gov/warehouse/stac/ugs-publications/collection.json", OURS)).toBe(true);
  });

  // The bug this closes: three 404s per item view against a bucket that never had items.json.
  it("never asks a federated catalog for one", () => {
    expect(hasItemsIndex("https://ubm-assets.geology.utah.gov/stac/ubm-climatology/collection.json", OURS)).toBe(false);
  });

  it("treats a relative href as ours", () => {
    expect(hasItemsIndex("./ugs-rasters/collection.json", location.href)).toBe(true);
  });

  it("says no rather than throwing on a href it can't parse", () => {
    expect(hasItemsIndex("::not a url::", OURS)).toBe(false);
  });
});


// A raster item carries the canonical COG in its source projection AND a reprojected derivative.
// Picking the first COG in the assets object drew the native one and the map threw
// "COG projection EPSG:26912 is not supported" (warehouse#84).
const COG = "image/tiff; application=geotiff; profile=cloud-optimized";
const twoCogs = {
  assets: {
    cog: { href: "https://x/a.cog.tif", type: COG, roles: ["data"] },
    visual: { href: "https://x/a_3857.cog.tif", type: COG, roles: ["visual"], "proj:code": "EPSG:3857" },
  },
  properties: { "proj:code": "EPSG:26912" },
};

describe("cog asset selection", () => {
  it("draws the visual derivative, not whichever COG comes first", () => {
    expect(cogRenderAsset(twoCogs)?.href).toBe("https://x/a_3857.cog.tif");
    expect(cogAsset(twoCogs)?.href).toBe("https://x/a_3857.cog.tif");
  });

  it("draws the visual derivative from an items.json entry, which has no proj:code", () => {
    const entry = { assets: { cog: { href: "https://x/a.cog.tif", type: COG, roles: ["data"] },
      visual: { href: "https://x/a_3857.cog.tif", type: COG, roles: ["visual"] } } };
    expect(cogRenderAsset(entry)?.href).toBe("https://x/a_3857.cog.tif");
  });

  it("draws a lone COG that is already web mercator, or says nothing to draw", () => {
    const merc = { assets: { cog: { href: "https://x/m.cog.tif", type: COG, roles: ["data"], "proj:code": "EPSG:3857" } } };
    expect(cogRenderAsset(merc)?.href).toBe("https://x/m.cog.tif");

    // Native CRS and no derivative: the item has raster data but no render path. Drawing nothing
    // beats throwing on a projection this client cannot reproject.
    const native = { assets: { cog: { href: "https://x/n.cog.tif", type: COG, roles: ["data"], "proj:code": "EPSG:26912" } } };
    expect(cogRenderAsset(native)).toBeUndefined();
    // It is still a raster item for classification purposes.
    expect(cogAsset(native)?.href).toBe("https://x/n.cog.tif");
  });
});


// The warehouse stamps a per-asset usage `description` (display vs query vs download) so a
// consumer picks the right endpoint without hardcoding asset keys (warehouse#280). This surfaces
// them for a listing; it does not change how anything is rendered.
describe("assetUsages", () => {
  it("pairs each asset that has a usage description with its key", () => {
    const doc = {
      assets: {
        data: { href: "https://x/a.parquet", type: "application/vnd.apache.parquet", roles: ["data"], description: "GeoParquet — download" },
        pmtiles: { href: "https://x/a.pmtiles", type: "application/vnd.pmtiles", roles: ["visual"], description: "Vector tiles — display" },
        thumbnail: { href: "https://x/t.png", type: "image/png", roles: ["thumbnail"] },
      },
    };
    const byKey = Object.fromEntries(assetUsages(doc).map((u) => [u.key, u.usage]));
    expect(byKey.data).toContain("download");
    expect(byKey.pmtiles).toContain("display");
    // An asset with no description is omitted rather than surfaced with an empty hint.
    expect(byKey.thumbnail).toBeUndefined();
  });

  it("is empty when the doc has no assets", () => {
    expect(assetUsages(undefined)).toEqual([]);
  });
});

describe("isDrawableCog", () => {
  it("is what decides the default tab and whether a preview mounts", () => {
    const native = twoCogs.assets.cog;
    const merc = twoCogs.assets.visual;
    // The item says EPSG:26912; the derivative overrides it on the asset.
    expect(isDrawableCog(merc, twoCogs)).toBe(true);
    expect(isDrawableCog(native, twoCogs)).toBe(false);
  });

  it("treats an item that states no projection as drawable", () => {
    const plain = { assets: { cog: { href: "https://x/p.cog.tif", type: COG, roles: ["data"] } } };
    expect(isDrawableCog(plain.assets.cog, plain)).toBe(true);
  });
});

describe("relatedJoins", () => {
  // Real shape (hazards_floodanddebrisflow): the related child asset carries the FK back to the
  // clicked parent — child.relate_id = parent.flhhazardunit.
  const doc: StacDoc = {
    assets: {
      data: { href: "https://x/parent.parquet", roles: ["data"] },
      hazards_unitdescriptions: {
        href: "https://x/unitdescriptions.parquet", title: "Unit Descriptions", roles: ["data", "related"],
        "ugs:foreign_keys": [{ fields: ["relate_id"], reference: { resource: "hazards_floodanddebrisflow", fields: ["flhhazardunit"] } }],
      },
    },
  };

  it("resolves a related asset's FK into a click-join descriptor (child field, parent field)", () => {
    expect(relatedJoins(doc)).toEqual([{
      key: "hazards_unitdescriptions", title: "Unit Descriptions", href: "https://x/unitdescriptions.parquet",
      childField: "relate_id", parentField: "flhhazardunit",
    }]);
  });

  it("falls back to the asset key when the related asset has no title", () => {
    const noTitle: StacDoc = { assets: { child: {
      href: "https://x/c.parquet", roles: ["related"],
      "ugs:foreign_keys": [{ fields: ["fk"], reference: { resource: "p", fields: ["pk"] } }],
    } } };
    expect(relatedJoins(noTitle)[0].title).toBe("child");
  });

  it("skips composite (multi-column) keys — a partial join would be wrong data (not yet supported)", () => {
    const composite: StacDoc = { assets: { child: {
      href: "https://x/c.parquet", roles: ["related"],
      "ugs:foreign_keys": [{ fields: ["a", "b"], reference: { resource: "p", fields: ["x", "y"] } }],
    } } };
    expect(relatedJoins(composite)).toEqual([]);
  });

  it("is empty when there are no related assets", () => {
    expect(relatedJoins({ assets: { data: { href: "https://x/p.parquet", roles: ["data"] } } })).toEqual([]);
    expect(relatedJoins(undefined)).toEqual([]);
  });
});

describe("collKeyOf", () => {
  const base = "https://maps-assets.geology.utah.gov/warehouse/stac/";
  it("keeps the full multi-segment path for a nested collection", () => {
    expect(collKeyOf(`${base}ugs-serving-topics/wetlands/collection.json`)).toBe("ugs-serving-topics/wetlands");
  });
  it("derives the collection key from an item href (drops /<id>/<id>.json)", () => {
    expect(collKeyOf(`${base}ugs-serving-topics/wetlands/wetlands_plants_site/wetlands_plants_site.json`))
      .toBe("ugs-serving-topics/wetlands");
  });
  it("handles a single-segment collection", () => {
    expect(collKeyOf(`${base}ugs-publications/collection.json`)).toBe("ugs-publications");
  });
  it("ignores a ?query / #hash on the href", () => {
    expect(collKeyOf(`${base}ugs-serving-topics/wetlands/wetlands_plants_site/wetlands_plants_site.json?t=1#x`))
      .toBe("ugs-serving-topics/wetlands");
  });
  it("is idempotent on a bare key, and undefined for no href", () => {
    expect(collKeyOf("ugs-serving-topics/wetlands")).toBe("ugs-serving-topics/wetlands");
    expect(collKeyOf(undefined)).toBeUndefined();
  });
});

describe("catalogItemHref", () => {
  const base = "https://maps-assets.geology.utah.gov/warehouse/stac/";
  // A nested-collection related link must resolve to the FULL collection key. Capturing only the last
  // folder (c=wetlands, not c=ugs-serving-topics/wetlands) never matched a loaded collection, so the
  // item page hung on "Loading…", the blank page in warehouse#348.
  it("routes a nested-collection item to the full-path c=", () => {
    expect(catalogItemHref(`${base}ugs-serving-topics/wetlands/wetlands_plants_projects/wetlands_plants_projects.json`))
      .toBe("?c=ugs-serving-topics%2Fwetlands&i=wetlands_plants_projects");
  });
  it("routes a single-segment-collection item", () => {
    expect(catalogItemHref(`${base}ugs-publications/OFR-123/OFR-123.json`)).toBe("?c=ugs-publications&i=OFR-123");
  });
  it("resolves a relative catalog href and ignores a ?query / #hash", () => {
    expect(catalogItemHref("ugs-publications/OFR-123/OFR-123.json")).toBe("?c=ugs-publications&i=OFR-123");
    expect(catalogItemHref(`${base}ugs-publications/OFR-123/OFR-123.json?t=1#x`)).toBe("?c=ugs-publications&i=OFR-123");
  });
  it("leaves a foreign (non-catalog) href as a direct link", () => {
    const foreign = "https://ubm-assets.geology.utah.gov/stac/ubm-x/item/item.json";
    expect(catalogItemHref(foreign)).toBe(foreign);
  });
});

describe("rasterTilesAsset", () => {
  const pm = (extra: Record<string, unknown>) => ({ href: "https://x/a.pmtiles", type: "application/vnd.pmtiles", roles: ["visual"], ...extra });
  it("finds the raster mosaic by its ugs:render mark", () => {
    const d: StacDoc = { assets: { tiles: pm({ "ugs:render": "raster" }) } };
    expect(rasterTilesAsset(d)?.href).toBe("https://x/a.pmtiles");
  });
  it("leaves a vector layer's visual PMTiles alone", () => {
    const d: StacDoc = { assets: { pmtiles: pm({}) } };
    expect(rasterTilesAsset(d)).toBeUndefined();
  });
});


describe("rootIndexItems", () => {
  it("resolves each self link from the root and skips an entry without one, warning once", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const idx = { items: [
      { id: "M-1", links: [{ rel: "self", href: "./ugs-publications/M/M-1/M-1.json" }] },
      { id: "broken", links: [] },
    ] } as never;
    const refs = rootIndexItems(idx);
    rootIndexItems(idx);
    expect(warn).toHaveBeenCalledTimes(1);
    expect(warn.mock.calls[0][0]).toContain("1 entry without a self link");
    warn.mockRestore();
    expect(refs).toHaveLength(1);
    expect(refs[0].href).toBe(new URL("./ugs-publications/M/M-1/M-1.json", ROOT_INDEX_URL).href);
    expect(refs[0].collId).toBe("ugs-publications/M");
  });

  it("files a root-relative collection.json self under its series", () => {
    const [ref] = rootIndexItems({ items: [
      { id: "OFR-9", links: [{ rel: "self", href: "./ugs-publications/OFR/OFR-9/collection.json" }] },
    ] } as never);
    expect(ref.href).toBe(new URL("./ugs-publications/OFR/OFR-9/collection.json", ROOT_INDEX_URL).href);
    expect(ref.collId).toBe("ugs-publications/OFR");
    expect(idOf(ref.href)).toBe("OFR-9");
  });
});

// A migrated publication series indexes one entry per publication Collection, self-linked to
// `<series>/<SID>/collection.json`; its Items sit at `<series>/<SID>/<item>/<item>.json`.
describe("publication Collection hrefs", () => {
  const base = "https://maps-assets.geology.utah.gov/warehouse/stac/";
  const pub = `${base}ugs-publications/OFR/OFR-9/collection.json`;
  it("keys a publication Collection by its own folder and names it by its SID", () => {
    expect(collKeyOf(pub)).toBe("ugs-publications/OFR/OFR-9");
    expect(idOf(pub)).toBe("OFR-9");
  });
  it("lists a publication under its series, and an item under its collection", () => {
    expect(entryCollKeyOf(pub)).toBe("ugs-publications/OFR");
    expect(entryCollKeyOf(`${base}ugs-publications/M/M-1/M-1.json`)).toBe("ugs-publications/M");
    expect(entryCollKeyOf(`${base}ugs-publications/OFR/OFR-9/plate/plate.json`)).toBe("ugs-publications/OFR/OFR-9");
    expect(entryCollKeyOf(`${base}ugs-rasters/collection.json`)).toBeUndefined();
  });
  it("routes a publication to its series and an Item inside it to the publication", () => {
    expect(catalogItemHref(pub)).toBe("?c=ugs-publications%2FOFR&i=OFR-9");
    expect(catalogItemHref(`${base}ugs-publications/OFR/OFR-9/plate/plate.json`))
      .toBe("?c=ugs-publications%2FOFR%2FOFR-9&i=plate");
  });
  it("tells a catalog href from a collection href", () => {
    expect(isCatalogHref(`${base}ugs-publications/OFR/catalog.json?x=1`)).toBe(true);
    expect(isCatalogHref(pub)).toBe(false);
    expect(isCollectionHref(pub)).toBe(true);
  });
});

describe("pubHrefFor", () => {
  const series = { id: "ugs-publications/OFR",
    href: "https://maps-assets.geology.utah.gov/warehouse/stac/ugs-publications/OFR/catalog.json" };
  const dir = "https://maps-assets.geology.utah.gov/warehouse/stac/ugs-publications/OFR/";
  it("builds a publication's collection.json from c = <series>", () => {
    expect(pubHrefFor(series, "ugs-publications/OFR", "OFR-9")).toBe(`${dir}OFR-9/collection.json`);
  });
  it("builds an Item inside a publication from c = <series>/<SID>", () => {
    expect(pubHrefFor(series, "ugs-publications/OFR/OFR-9", "OFR-9_geounits"))
      .toBe(`${dir}OFR-9/OFR-9_geounits/OFR-9_geounits.json`);
    expect(pubSidIn(series.id, "ugs-publications/OFR/OFR-9")).toBe("OFR-9");
  });
  it("names nothing for another collection, a deeper path, or a collection-type series", () => {
    expect(pubHrefFor(series, "ugs-publications/B", "B-1")).toBeUndefined();
    expect(pubHrefFor(series, "ugs-publications/OFR/OFR-9/x", "y")).toBeUndefined();
    expect(pubHrefFor({ id: series.id, href: `${dir}collection.json` }, series.id, "OFR-9")).toBeUndefined();
    expect(pubSidIn(series.id, "ugs-publications/OFRX/1")).toBeUndefined();
  });
});

describe("publication layers in the root index", () => {
  const tagged: StacDoc = { id: "OFR-9_flows", properties: {
    title: "Debris Flow Paths", "ugs:in_publication": "OFR-9", "ugs:publication_title": "Geologic map of X" } };
  it("reads the publication a tagged entry belongs to, and labels the layer with it", () => {
    expect(pubOfEntry(tagged)).toEqual({ id: "OFR-9", title: "Geologic map of X" });
    expect(layerLabel(tagged, "x")).toBe("Debris Flow Paths · Geologic map of X");
  });
  it("ignores a raster's provenance ugs:pub_id, and labels by the id when the title is missing", () => {
    const raster: StacDoc = { id: "r", properties: { title: "Mosaic", "ugs:pub_id": "OFR-123" } };
    expect(pubOfEntry(raster)).toBeUndefined();
    expect(layerLabel(raster, "r")).toBe("Mosaic");
    expect(pubOfEntry({ properties: { "ugs:in_publication": "OFR-9" } })).toEqual({ id: "OFR-9", title: "OFR-9" });
  });
  it("keeps a tagged entry as a ref the map resolves by its item id", () => {
    const [ref] = rootIndexItems({ items: [{ ...tagged,
      links: [{ rel: "self", href: "./ugs-publications/OFR/OFR-9/OFR-9_flows/OFR-9_flows.json" }] }] });
    expect(ref.collId).toBe("ugs-publications/OFR/OFR-9");
    expect(idOf(ref.href)).toBe("OFR-9_flows");
  });
});

describe("indexItemRefs", () => {
  const series = "https://maps-assets.geology.utah.gov/warehouse/stac/ugs-publications/OFR/catalog.json";
  const seriesIndex = "https://maps-assets.geology.utah.gov/warehouse/stac/ugs-publications/OFR/items.json";
  it("resolves a series index's self links from where the index was read", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const r = { id: "ugs-publications/OFR", href: series, index: {
      url: seriesIndex,
      items: [
        { id: "OFR-9", links: [{ rel: "self", href: "./OFR-9/collection.json" }] },
        { id: "no-self", links: [] },
      ],
    } };
    const refs = indexItemRefs(r);
    indexItemRefs(r);
    expect(warn).toHaveBeenCalledTimes(1);
    expect(warn.mock.calls[0][0]).toContain(seriesIndex);
    warn.mockRestore();
    expect(refs).toHaveLength(1);
    expect(refs[0].collId).toBe("ugs-publications/OFR");
    expect(refs[0].href).toBe("https://maps-assets.geology.utah.gov/warehouse/stac/ugs-publications/OFR/OFR-9/collection.json");
    expect(entryCollKeyOf(refs[0].href)).toBe(refs[0].collId);
  });
  it("places a collection's items beside it, as before", () => {
    const coll = "https://maps-assets.geology.utah.gov/warehouse/stac/ugs-publications/B/collection.json";
    const refs = indexItemRefs({ id: "ugs-publications/B", href: coll, index: { items: [{ id: "B-1" }] } });
    expect(refs[0].href).toBe("https://maps-assets.geology.utah.gov/warehouse/stac/ugs-publications/B/B-1/B-1.json");
  });
  it("is empty before the index loads", () => {
    expect(indexItemRefs({ id: "x", href: series })).toEqual([]);
  });
});

const pubCollection = {
  type: "Collection", id: "OFR-9",
  title: "Geologic map of somewhere", description: "Full citation.",
  keywords: ["geology"], "ugs:series_id": "OFR-9", "ugs:pub_type": "Open-File Report",
  "ugs:author": "Doe, J.", "sci:doi": "10.34191/OFR-9", license: "CC-BY-4.0", stac_version: "1.1.0",
  extent: { spatial: { bbox: [[-112, 40, -111, 41]] },
            temporal: { interval: [["2020-01-01T00:00:00Z", "2020-01-01T00:00:00Z"]] } },
  assets: { publication: { href: "https://x/ofr-9.pdf", type: "application/pdf" } },
  links: [
    { rel: "parent", href: "../catalog.json" },
    { rel: "alternate", href: "https://geology.utah.gov/pub/OFR-9", type: "text/html", title: "UGS publication landing page" },
    { rel: "cite-as", href: "https://doi.org/10.34191/OFR-9" },
    { rel: "item", href: "./plate/plate.json", title: "Plate 1" },
    { rel: "item", href: "./units/units.json" },
  ],
} as StacDoc;

describe("collectionAsItem", () => {
  it("lifts a Collection's top-level metadata into properties and keeps assets and links", () => {
    const d = collectionAsItem(pubCollection);
    expect(d.properties).toMatchObject({
      title: "Geologic map of somewhere", description: "Full citation.", keywords: ["geology"],
      "ugs:series_id": "OFR-9", "ugs:pub_type": "Open-File Report", "ugs:author": "Doe, J.",
      "sci:doi": "10.34191/OFR-9", license: "CC-BY-4.0", datetime: "2020-01-01T00:00:00Z",
    });
    expect(d.properties).not.toHaveProperty("extent");
    expect(d.properties).not.toHaveProperty("stac_version");
    expect(d.bbox).toEqual([-112, 40, -111, 41]);
    expect(d.assets).toBe(pubCollection.assets);
    expect(d.links).toBe(pubCollection.links);
    expect(d.type).toBe("Collection");
  });
  it("leaves an undated publication without a datetime", () => {
    const d = collectionAsItem({ ...pubCollection, "ugs:date_unknown": true } as StacDoc);
    expect(d.properties).not.toHaveProperty("datetime");
    expect(d.properties?.["ugs:date_unknown"]).toBe(true);
  });
  it("passes an Item through untouched", () => {
    const item: StacDoc = { type: "Feature", id: "x", properties: { title: "X" } };
    expect(collectionAsItem(item)).toBe(item);
  });
});

describe("placeholder footprints", () => {
  const utah = [-114.05, 37, -109.04, 42];
  it("is no footprint when flagged utah-bbox, or when there is no geometry or bbox", () => {
    expect(hasFootprint({ bbox: utah, properties: { "ugs:footprint_source": "utah-bbox" } })).toBe(false);
    expect(isPlaceholderFootprint({ properties: { "ugs:footprint_source": "utah-bbox" } })).toBe(true);
    expect(hasFootprint({ geometry: null, properties: {} })).toBe(false);
    expect(hasFootprint({ bbox: [-112, 40, -111, 41], properties: { "ugs:footprint_source": "cog" } })).toBe(true);
    expect(hasFootprint(undefined)).toBe(false);
  });
  it("keeps a placeholder Collection extent out of the normalised bbox", () => {
    const d = collectionAsItem({ ...pubCollection, "ugs:footprint_source": "utah-bbox",
      extent: { spatial: { bbox: [utah] }, temporal: { interval: [[null, null]] } } } as StacDoc);
    expect(d.bbox).toBeUndefined();
    expect(hasFootprint(d)).toBe(false);
  });
});

describe("itemMissing", () => {
  it("is missing when the doc 404s", () => {
    expect(itemMissing({ itemUrl: "MD-1002", itemHref: "https://x/MD-1002/collection.json",
      error: new HttpError(404, "404 Not Found"), resolving: false })).toBe(true);
  });
  it("is missing once everything that could resolve the id has loaded without a match", () => {
    expect(itemMissing({ itemUrl: "MD-1002", resolving: false })).toBe(true);
    expect(itemMissing({ itemUrl: "MD-1002", resolving: true })).toBe(false);
  });
  it("is not missing while the collection.json fallback is still fetching", () => {
    // The leaf's items.json 404'd (optional), so its item links are loading from collection.json.
    const r = resolutionStatus([
      { isLoading: false }, { isLoading: false },
      { isLoading: true },                                               // fallback collection.json
      { isLoading: false, error: new HttpError(404, "404"), optional: true },
    ]);
    expect(r).toEqual({ resolving: true, error: undefined });
    expect(itemMissing({ itemUrl: "B-1", resolving: r.resolving, failed: r.error != null })).toBe(false);
  });
  it("is unknown, not missing, when a lookup fails", () => {
    const offline = new TypeError("Failed to fetch");
    const r = resolutionStatus([{ isLoading: false, error: offline }, { isLoading: false }]);
    expect(r.error).toBe(offline);
    expect(itemMissing({ itemUrl: "B-1", resolving: false, failed: true })).toBe(false);
    // An absent index (404, or a catalog with no index link) is expected, not a failure.
    expect(resolutionStatus([{ isLoading: false, error: new NoIndexError("no link"), optional: true }]).error)
      .toBeUndefined();
    expect(resolutionStatus([{ isLoading: false, error: new HttpError(503, "503"), optional: true }]).error)
      .toBeInstanceOf(HttpError);
  });
  it("is not missing for a doc that exists, another failure, or no selection", () => {
    expect(itemMissing({ itemUrl: "MD-10", itemHref: "https://x/a.json", resolving: false })).toBe(false);
    expect(itemMissing({ itemUrl: "MD-10", itemHref: "https://x/a.json",
      error: new HttpError(500, "500"), resolving: false })).toBe(false);
    expect(itemMissing({ resolving: false })).toBe(false);
  });
});

describe("collectionParts", () => {
  it("lists a Collection's Items from its rel=item links, titled where the link is", () => {
    expect(collectionParts(pubCollection)).toEqual([
      { id: "plate", title: "Plate 1" }, { id: "units", title: "units" },
    ]);
  });
  it("is empty for a report-only publication and for an Item", () => {
    expect(collectionParts({ ...pubCollection, links: [] })).toEqual([]);
    expect(collectionParts({ type: "Feature", links: [{ rel: "item", href: "./a/a.json" }] })).toEqual([]);
  });
});

describe("publication links", () => {
  it("reads the landing page from rel=via, else an HTML rel=alternate", () => {
    expect(viaLink(pubCollection)?.href).toBe("https://geology.utah.gov/pub/OFR-9");
    expect(viaLink({ links: [{ rel: "alternate", href: "https://x/a.json", type: "application/json" }] })).toBeUndefined();
    expect(viaLink({ links: [
      { rel: "alternate", href: "https://x/a.html", type: "text/html" }, { rel: "via", href: "https://x/via" },
    ] })?.href).toBe("https://x/via");
    expect(citeLink(pubCollection)?.href).toBe("https://doi.org/10.34191/OFR-9");
  });
  it("finds an Item's collection by rel=collection, else rel=parent", () => {
    expect(collectionLink({ links: [{ rel: "parent", href: "../collection.json" }] })?.href).toBe("../collection.json");
    expect(collectionLink({ links: [
      { rel: "parent", href: "../p.json" }, { rel: "collection", href: "../collection.json" },
    ] })?.href).toBe("../collection.json");
  });
});
