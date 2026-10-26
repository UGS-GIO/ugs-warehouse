// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { StacDoc } from "@/stac";

const parquet = { href: "https://x/a.parquet", type: "application/vnd.apache.parquet", roles: ["data"] };
const withParquet = (props: Record<string, unknown>, links: StacDoc["links"] = []): StacDoc => ({
  id: "a", properties: props, assets: { data: parquet }, links,
});

const cog = { href: "https://x/a.cog.tif", type: "image/tiff; application=geotiff; profile=cloud-optimized" };

// GEOSERVICES_BASE is read from ?geoservices= when stac.ts loads, so each test imports the panel
// after setting it.
const panel = async (item: StacDoc) => {
  const { PreviewMapProvider } = await import("@/map/preview-map");
  const { EndpointsPanel } = await import("./endpoints-panel");
  return render(<PreviewMapProvider><EndpointsPanel item={item} /></PreviewMapProvider>);
};

beforeEach(() => {
  vi.resetModules();
  window.history.replaceState(null, "", "/?geoservices=https://gs.example");
});
afterEach(() => { window.history.replaceState(null, "", "/"); });

describe("EndpointsPanel service links", () => {
  it("offers the FeatureServer and OGC API Features for a serving topic", async () => {
    await panel(withParquet({ "ugs:dbt_schema": "hazards" },
      [{ rel: "service", href: "https://fs.example/collections/a", type: "application/json" }]));
    expect(screen.getByText("ArcGIS feature layer")).toBeDefined();
    expect(screen.getByText("OGC API Features")).toBeDefined();
    const mv = screen.getByRole("link", { name: "Open in Map Viewer" }) as HTMLAnchorElement;
    expect(mv.href).toBe(`https://www.arcgis.com/apps/mapviewer/index.html?url=${
      encodeURIComponent("https://gs.example/ugs/rest/services/a/default/FeatureServer")}`);
    expect(mv.target).toBe("_blank");
  });

  it("offers the ImageServer for a publication with a COG", async () => {
    await panel({ id: "MP-01-1", collection: "MP", properties: {}, assets: { cog } });
    expect(screen.getByText("ArcGIS image layer")).toBeDefined();
    expect((screen.getByRole("link", { name: "Open in Map Viewer" }) as HTMLAnchorElement).href).toContain(
      encodeURIComponent("https://gs.example/ugs/rest/services/MP-01-1/ImageServer"));
    expect(screen.getAllByText("copy")).toHaveLength(1);
  });

  it("offers none for any item without the ugs:dbt_schema stamp", async () => {
    const { container } = await panel(withParquet({}));
    expect(container.textContent).toBe("");
  });

  it("offers none for a publication layer, which neither service serves", async () => {
    const { container } = await panel(withParquet({ "ugs:series_id": "M-246DM" }));
    expect(screen.queryByText("ArcGIS feature layer")).toBeNull();
    expect(container.textContent).toBe("");
  });
});

describe("isImageServed", () => {
  const served = async (d: StacDoc) => (await import("@/stac")).isImageServed(d);

  it("serves a COG keyed visual, cog or data in a listed collection", async () => {
    expect(await served({ collection: "MP", assets: { cog } })).toBe(true);
    expect(await served({ collection: "geolmap_24k_series", assets: { visual: cog } })).toBe(true);
    expect(await served({ collection: "ugs-geologic-maps", assets: { tiles: parquet, visual: cog } })).toBe(true);
    expect(await served({ collection: "OFR", assets: { data: { href: "https://x/a.tif" } } })).toBe(true);
  });

  it("skips a pub without a COG, another key, a non-TIFF, or an unlisted series", async () => {
    expect(await served({ collection: "MP", assets: { publication: { href: "a.pdf", type: "application/pdf" } } })).toBe(false);
    // An index entry: no `collection`, only a self link under its series.
    const self = (href: string) => [{ rel: "self", href }];
    expect(await served({ links: self("./ugs-publications/M/M-1/M-1.json"), assets: { cog } })).toBe(true);
    expect(await served({ links: self("./ugs-publications/RI/RI-1/RI-1.json"), assets: { cog } })).toBe(false);
    expect(await served({ collection: "MP", assets: { image: cog } })).toBe(false);
    expect(await served({ collection: "hazards", assets: { data: parquet } })).toBe(false);
    expect(await served({ collection: "RI", assets: { cog } })).toBe(false);
    expect(await served({ assets: { cog } })).toBe(false);
  });
});

describe("service URL builders", () => {
  it("build the FeatureServer and ImageServer service URLs from ?geoservices=", async () => {
    const { featureServerUrl, imageServerUrl } = await import("@/stac");
    expect(featureServerUrl("a b", "geo")).toBe("https://gs.example/ugs/rest/services/a%20b/geo/FeatureServer");
    expect(imageServerUrl("MP-01-1")).toBe("https://gs.example/ugs/rest/services/MP-01-1/ImageServer");
  });

  it("build nothing without a geoservices base", async () => {
    window.history.replaceState(null, "", "/");
    const { featureServerUrl, imageServerUrl } = await import("@/stac");
    expect(featureServerUrl("a")).toBeUndefined();
    expect(imageServerUrl("a")).toBeUndefined();
  });
});
