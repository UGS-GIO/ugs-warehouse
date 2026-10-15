// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PreviewMapProvider } from "@/map/preview-map";
import { collectionAsItem, type StacDoc, stacDocQuery } from "@/stac";
import { Preview } from "./asset-viewer";
import { PlateAddToMap } from "./item-detail";

// The view context, with the real app's layer gate: drawsAsLayer is toLayer's, as in app.tsx. Add to
// map goes through addLayer, the same call the plate's own page makes.
const ctx = vi.hoisted(() => ({
  itemUrl: undefined as string | undefined,
  isLayerId: (_id: string): boolean => false,
  drawsAsLayer: (_d: unknown): boolean => false,
  isActive: (_id: string): boolean => false,
  addLayer: vi.fn(),
  removeLayer: vi.fn(),
}));
vi.mock("@/app", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/app")>();
  ctx.drawsAsLayer = (d: unknown) => real.toLayer({ collId: "", href: "layer/probe", data: d as StacDoc }) !== null;
  return { ...real, useViewCtx: () => ctx };
});

const COLL_URL = "https://cdn/stac/ugs-publications/M/M-246DM/collection.json";
const PLATE_URL = "https://cdn/stac/ugs-publications/M/M-246DM/M-246DM_plate/M-246DM_plate.json";
const COG = "image/tiff; application=geotiff; profile=cloud-optimized";
const collection = {
  type: "Collection", id: "M-246DM", title: "Landslide maps of Utah",
  extent: { spatial: { bbox: [[-113, 37.5, -109, 41.7]] }, temporal: { interval: [["2010-01-01T00:00:00Z", null]] } },
  assets: { publication: { href: "https://x/m.pdf", type: "application/pdf", title: "Publication" } },
  links: [{ rel: "item", href: "./M-246DM_plate/M-246DM_plate.json", title: "Map plate" }],
};
const plate = {
  type: "Feature", id: "M-246DM_plate", properties: { title: "Map plate" },
  assets: { cog: { href: "https://x/M-246DM.cog.tif", type: COG, roles: ["data", "cloud-optimized", "visual"] } },
  links: [],
};

let plateStatus = 200;
beforeEach(() => {
  plateStatus = 200;
  ctx.addLayer.mockClear();
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    if (url === COLL_URL) return new Response(JSON.stringify(collection));
    if (url === PLATE_URL) {
      return plateStatus === 200 ? new Response(JSON.stringify(plate))
        : new Response("oops", { status: plateStatus, statusText: "Internal Server Error" });
    }
    return new Response("not found", { status: 404, statusText: "Not Found" });
  });
});
afterEach(() => vi.restoreAllMocks());

// The publication as the page gets it: fetched (so its relative links resolve), then item-shaped.
const loadPublication = async () => collectionAsItem(await stacDocQuery(COLL_URL).queryFn());

const renderPage = (item: StacDoc) => render(
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retryDelay: 0 } } })}>
    <PreviewMapProvider>
      <PlateAddToMap item={item} />
      <Preview item={item} />
    </PreviewMapProvider>
  </QueryClientProvider>,
);

describe("the plate on a publication page", () => {
  it("adds a Map tab beside the PDF and an Add to map that adds the plate layer", async () => {
    renderPage(await loadPublication());
    const mapTab = await screen.findByRole("button", { name: /Map\s*Map plate/ });
    expect(mapTab).toBeDefined();
    expect(screen.getByRole("button", { name: /PDF\s*Publication/ })).toBeDefined();
    await userEvent.click(await screen.findByRole("button", { name: /Add to map/ }));
    expect(ctx.addLayer).toHaveBeenCalledWith("M-246DM_plate");
  });

  it("says the plate could not be loaded instead of leaving it out silently", async () => {
    plateStatus = 500;
    renderPage(await loadPublication());
    expect((await screen.findByRole("alert")).textContent).toContain("Map plate could not be loaded");
    expect(screen.queryByRole("button", { name: /Add to map/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Map\s*Map plate/ })).toBeNull();
  });
});

describe("the publication's other files", () => {
  it("leave out the GeoPackage, which has its own download link", async () => {
    const item = await loadPublication();
    item.assets = {
      ...item.assets,
      gis_zip: { href: "https://x/gis.zip", type: "application/zip", title: "GIS Data - Zip", roles: ["source"] },
      geopackage: { href: "https://x/M-246DM.gpkg", type: "application/geopackage+sqlite3",
                    title: "All data (GeoPackage)", roles: ["data"] },
    };
    renderPage(item);
    const other = await screen.findByText(/Other files:/);
    expect(other.textContent).toContain("GIS Data - Zip");
    expect(other.textContent).not.toContain("GeoPackage");
  });
});
