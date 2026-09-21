// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DownloadsPanel } from "./downloads-panel";
import type { StacDoc } from "@/stac";

// `vi.mock` is hoisted above the file's consts, so the spies have to be hoisted with it.
const { exportItem, shapefileWarnings } = vi.hoisted(() => ({
  exportItem: vi.fn(), shapefileWarnings: vi.fn(),
}));
vi.mock("@/data/download", () => ({ exportItem, shapefileWarnings }));

const HREF = "https://cdn.example/x.parquet";
const item: StacDoc = {
  id: "x", type: "Feature", bbox: [-114, 37, -109, 42],
  assets: {
    data: { href: HREF, type: "application/vnd.apache.parquet", title: "GeoParquet archive" },
    metadata: { href: "https://cdn.example/x.xml", type: "application/xml", title: "ISO 19139 metadata" },
  },
};

const MANGLED = {
  any: true, mixedGeometry: ["POINT", "LINESTRING"], longNames: [], collisions: [],
  tooManyFields: false, over2gb: false, fieldCount: 3, estShpBytes: 10, estDbfBytes: 10,
  estPeakBytes: 10, overBrowserLimit: false, sourceBytes: 1000, tooBigToInspect: false,
};

const show = () => render(
  <QueryClientProvider client={new QueryClient({ defaultOptions: { mutations: { retry: false } } })}>
    <DownloadsPanel item={item} />
  </QueryClientProvider>,
);

beforeEach(() => { exportItem.mockClear(); shapefileWarnings.mockClear(); });

describe("DownloadsPanel", () => {
  it("lists the item's files and every format we can build, as one grid", () => {
    show();
    for (const label of ["GeoParquet archive", "ISO 19139 metadata", "Shapefile (zip)",
      "GeoPackage", "File Geodatabase (zip)", "FlatGeobuf", "GeoJSON", "CSV (WKT)"]) {
      expect(screen.getByText(label), label).toBeDefined();
    }
  });

  it("exports straight away for a format with no pre-flight", async () => {
    show();
    await userEvent.click(screen.getByLabelText("Download GeoPackage"));
    expect(shapefileWarnings).not.toHaveBeenCalled();
    expect(exportItem).toHaveBeenCalledWith(HREF, "x", "gpkg", undefined, 4326);
  });

  it("holds back a shapefile that would be mangled", async () => {
    shapefileWarnings.mockResolvedValue(MANGLED);
    show();
    await userEvent.click(screen.getByLabelText("Download Shapefile (zip)"));
    expect(await screen.findByText(/will mangle this data/i)).toBeDefined();
    expect(exportItem).not.toHaveBeenCalled();
  });

  // The bypass is a `force` variable on the same mutation, so it can't skip the check by accident.
  it("exports the shapefile anyway when asked, without re-running the check", async () => {
    shapefileWarnings.mockResolvedValue(MANGLED);
    show();
    await userEvent.click(screen.getByLabelText("Download Shapefile (zip)"));
    await userEvent.click(await screen.findByText("Download shapefile anyway"));
    expect(exportItem).toHaveBeenCalledWith(HREF, "x", "shp", undefined, 4326);
    expect(shapefileWarnings).toHaveBeenCalledTimes(1);
  });

  it("offers GeoPackage as the way out, and drops the warning", async () => {
    shapefileWarnings.mockResolvedValue(MANGLED);
    show();
    await userEvent.click(screen.getByLabelText("Download Shapefile (zip)"));
    await userEvent.click(await screen.findByText("Use GeoPackage instead"));
    expect(exportItem).toHaveBeenCalledWith(HREF, "x", "gpkg", undefined, 4326);
    expect(screen.queryByText(/will mangle this data/i)).toBeNull();
  });

  it("proceeds when the pre-flight itself fails", async () => {
    shapefileWarnings.mockRejectedValue(new Error("read failed"));
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
