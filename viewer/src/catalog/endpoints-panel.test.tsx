// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { StacDoc } from "@/stac";

const parquet = { href: "https://x/a.parquet", type: "application/vnd.apache.parquet", roles: ["data"] };
const withParquet = (props: Record<string, unknown>, links: StacDoc["links"] = []): StacDoc => ({
  id: "a", properties: props, assets: { data: parquet }, links,
});

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
