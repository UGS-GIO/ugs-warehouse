import { describe, expect, it } from "vitest";
import { isCatalogJson } from "./sw-routes";

const match = (href: string, headers?: Record<string, string>) =>
  isCatalogJson({ url: new URL(href), request: new Request(href, { headers }) });

const CDN = "https://maps-assets.geology.utah.gov";

describe("isCatalogJson", () => {
  it("matches the catalog docs the viewer reads", () => {
    expect(match(`${CDN}/warehouse/stac/catalog.json`)).toBe(true);
    expect(match(`${CDN}/warehouse/stac/ugs-serving-topics/hazards/collection.json`)).toBe(true);
    expect(match(`${CDN}/warehouse/stac/items.json`)).toBe(true);
  });

  it("matches the review catalog too, so one rule covers both deploys", () => {
    expect(match(`${CDN}/review/stac/catalog.json`)).toBe(true);
  });

  // The whole point of #351: these are single files read by 206 partials, and the Cache API cannot
  // serve a range out of a stored full response.
  it("never matches a range request", () => {
    expect(match(`${CDN}/warehouse/stac/catalog.json`, { range: "bytes=0-16383" })).toBe(false);
  });

  it("never matches layer data or non-catalog JSON", () => {
    expect(match(`${CDN}/warehouse/pmtiles/hazards_qfaults_current.pmtiles`)).toBe(false);
    expect(match(`${CDN}/warehouse/archive/hazards_qfaults_current.parquet`)).toBe(false);
    expect(match(`${CDN}/pubs/search/corpus.json`)).toBe(false);
    expect(match(`${CDN}/styles/styles/enmin_plss_sections/default.json`)).toBe(false);
  });

  // Workbox types `request` as optional, and a match can be driven programmatically.
  it("survives a match with no request", () => {
    expect(isCatalogJson({ url: new URL(`${CDN}/warehouse/stac/catalog.json`) })).toBe(true);
  });
});
