// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DownloadsPanel } from "./downloads-panel";
import type { StacDoc } from "@/stac";

// `vi.mock` is hoisted above the file's consts, so the spies have to be hoisted with it.
const { exportItem, exportWarnings } = vi.hoisted(() => ({
  exportItem: vi.fn(), exportWarnings: vi.fn(),
}));
vi.mock("@/data/download", () => ({ exportItem, exportWarnings }));

const HREF = "https://cdn.example/x.parquet";
const item: StacDoc = {
  id: "x", type: "Feature", bbox: [-114, 37, -109, 42],
  assets: {
    data: { href: HREF, type: "application/vnd.apache.parquet", title: "GeoParquet archive" },
    metadata: { href: "https://cdn.example/x.xml", type: "application/xml", title: "ISO 19139 metadata" },
  },
};

const CLEAN = {
  any: false, mixedGeometry: [] as string[], longNames: [] as string[],
  collisions: [] as [string, string][], tooManyFields: false, over2gb: false, fieldCount: 3,
  estShpBytes: 10, estDbfBytes: 10, estPeakBytes: 10, overBrowserLimit: false,
  sourceBytes: 1000, tooBigToInspect: false,
};
const MANGLED = { ...CLEAN, any: true, mixedGeometry: ["POINT", "LINESTRING"] };
// Past the tab's memory ceiling: GeoPackage runs in the same wasm instance, so it is no way out.
const TOO_BIG = { ...CLEAN, any: true, overBrowserLimit: true, tooBigToInspect: true,
  sourceBytes: 1_351_235_892 };

const show = () => render(
  <QueryClientProvider client={new QueryClient({ defaultOptions: { mutations: { retry: false } } })}>
    <DownloadsPanel item={item} />
  </QueryClientProvider>,
);

beforeEach(() => {
  exportItem.mockClear();
  exportWarnings.mockReset();
  exportWarnings.mockResolvedValue(CLEAN);
});

describe("DownloadsPanel", () => {
  it("lists the item's files and every format we can build, as one grid", () => {
    show();
    for (const label of ["GeoParquet archive", "ISO 19139 metadata", "Shapefile (zip)",
      "GeoPackage", "File Geodatabase (zip)", "FlatGeobuf", "GeoJSON", "CSV (WKT)"]) {
      expect(screen.getByText(label), label).toBeDefined();
    }
  });

  // Every format loads the whole GeoParquet into the tab, so every format is pre-flighted.
  it("pre-flights every format, not only the shapefile", async () => {
    show();
    await userEvent.click(screen.getByLabelText("Download GeoPackage"));
    expect(exportWarnings).toHaveBeenCalledWith(HREF, "gpkg", undefined);
    expect(exportItem).toHaveBeenCalledWith(HREF, "x", "gpkg", undefined, 4326);
  });

  it("holds back a GeoPackage the tab cannot hold, instead of crashing", async () => {
    exportWarnings.mockResolvedValue(TOO_BIG);
    show();
    await userEvent.click(screen.getByLabelText("Download GeoPackage"));
    expect(await screen.findByText(/too big to convert in the browser/i)).toBeDefined();
    expect(exportItem).not.toHaveBeenCalled();
  });

  it("does not offer GeoPackage as a way out of a memory ceiling it shares", async () => {
    exportWarnings.mockResolvedValue(TOO_BIG);
    show();
    await userEvent.click(screen.getByLabelText("Download Shapefile (zip)"));
    await screen.findByText(/too big to convert in the browser/i);
    expect(screen.queryByText("Use GeoPackage instead")).toBeNull();
  });

  it("holds back a shapefile that would be mangled", async () => {
    exportWarnings.mockResolvedValue(MANGLED);
    show();
    await userEvent.click(screen.getByLabelText("Download Shapefile (zip)"));
    expect(await screen.findByText(/will mangle this data/i)).toBeDefined();
    expect(exportItem).not.toHaveBeenCalled();
  });

  // The bypass is a `force` variable on the same mutation, so it can't skip the check by accident.
  it("exports the shapefile anyway when asked, without re-running the check", async () => {
    exportWarnings.mockResolvedValue(MANGLED);
    show();
    await userEvent.click(screen.getByLabelText("Download Shapefile (zip)"));
    await userEvent.click(await screen.findByText("Download anyway"));
    expect(exportItem).toHaveBeenCalledWith(HREF, "x", "shp", undefined, 4326);
    expect(exportWarnings).toHaveBeenCalledTimes(1);
  });

  it("offers GeoPackage as the way out, and drops the warning", async () => {
    // Faithful to the real call: the shapefile-only findings are empty for any other format.
    exportWarnings.mockImplementation((_u: string, fmt: string) =>
      Promise.resolve(fmt === "shp" ? MANGLED : CLEAN));
    show();
    await userEvent.click(screen.getByLabelText("Download Shapefile (zip)"));
    await userEvent.click(await screen.findByText("Use GeoPackage instead"));
    expect(exportItem).toHaveBeenCalledWith(HREF, "x", "gpkg", undefined, 4326);
    expect(screen.queryByText(/will mangle this data/i)).toBeNull();
  });

  it("proceeds when the pre-flight itself fails", async () => {
    exportWarnings.mockRejectedValue(new Error("read failed"));
    show();
    await userEvent.click(screen.getByLabelText("Download Shapefile (zip)"));
    expect(exportItem).toHaveBeenCalledWith(HREF, "x", "shp", undefined, 4326);
  });

  it("reports a failed export instead of failing silently", async () => {
    exportItem.mockRejectedValueOnce(new Error("gdal exploded"));
    show();
    await userEvent.click(screen.getByLabelText("Download GeoJSON"));
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", "Download failed: gdal exploded");
  });
});
