// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PreviewMapProvider } from "@/map/preview-map";
import type { StacDoc } from "@/stac";
import { EndpointsPanel } from "./endpoints-panel";

const pm = "https://x/a.pmtiles";
const withTiles = (props: Record<string, unknown>): StacDoc => ({
  id: "a", properties: props,
  links: [{ rel: "pmtiles", href: pm, type: "application/vnd.pmtiles", "pmtiles:layers": ["a"] }],
});
const panel = (item: StacDoc) => render(<PreviewMapProvider><EndpointsPanel item={item} /></PreviewMapProvider>);

describe("EndpointsPanel tile-service links", () => {
  it("offers the tiles service's links for a serving topic", () => {
    // No TILES_BASE in tests, so the ArcGIS row (which needs a published style) is the one that shows.
    panel(withTiles({ "ugs:dbt_schema": "hazards" }));
    expect(screen.getByText("ArcGIS vector tiles")).toBeDefined();
  });

  it("offers none for a publication layer, which the tiles service does not serve", () => {
    const { container } = panel(withTiles({ "ugs:series_id": "M-246DM" }));
    expect(screen.queryByText("ArcGIS vector tiles")).toBeNull();
    expect(container.textContent).toBe("");
  });
});
