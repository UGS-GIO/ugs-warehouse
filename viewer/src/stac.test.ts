// @vitest-environment jsdom
import { describe, expect, it } from "vitest";

import { cogAsset, cogRenderAsset, isDrawableCog, hasItemsIndex } from "./stac";

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
