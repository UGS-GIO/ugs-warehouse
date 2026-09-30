// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DownloadsPanel } from "./downloads-panel";
import type { StacDoc } from "@/stac";

// `vi.mock` is hoisted above the file's consts, so the spies have to be hoisted with it.
// A working stand-in for the run store, not a stub: the panel reads it through
// useSyncExternalStore to decide what is cancellable, so a store frozen at empty would hide the
// cancel path entirely. Keeps one snapshot reference between changes, which that hook requires.
const { exportItem, exportWarnings, beginExport, cancelExport, startRun, endRun, runs } =
  vi.hoisted(() => {
    const live = new Map<number, { id: number; stem: string; fmt: string }>();
    let snapshot: readonly unknown[] = [];
    const listeners = new Set<() => void>();
    const publish = () => { snapshot = [...live.values()]; listeners.forEach((l) => l()); };
    const runs = {
      currentExports: () => snapshot,
      subscribeExport: (l: () => void) => { listeners.add(l); return () => { listeners.delete(l); }; },
      reset: () => { live.clear(); publish(); },
    };
    return {
      exportItem: vi.fn(), exportWarnings: vi.fn(), beginExport: vi.fn(() => 7),
      cancelExport: vi.fn((id: number) => { if (live.delete(id)) publish(); }),
      startRun: vi.fn((r: { id: number; stem: string; fmt: string }) => { live.set(r.id, r); publish(); }),
      endRun: vi.fn((id: number) => { if (live.delete(id)) publish(); }),
      runs,
    };
  });
vi.mock("@/data/download", () => ({
  exportItem, exportWarnings, beginExport, cancelExport, startRun, endRun,
  holdsOneGeomType: (fmt: string) => fmt === "shp" || fmt === "gdb",
  SLOW_READ_BYTES: 256 * 1024 ** 2,
  // The panel subscribes to the module's own export state so a remount keeps the Cancel button.
  // The store has its own suite (export-runs.test.ts); here it stays empty so the panel's own
  // pending run is the only thing on screen.
  currentExports: runs.currentExports, subscribeExport: runs.subscribeExport,
}));

const HREF = "https://cdn.example/x.parquet";
const item: StacDoc = {
  id: "x", type: "Feature", bbox: [-114, 37, -109, 42],
  assets: {
    data: { href: HREF, type: "application/vnd.apache.parquet", title: "GeoParquet archive" },
    metadata: { href: "https://cdn.example/x.xml", type: "application/xml", title: "ISO 19139 metadata" },
  },
};

const CLEAN = {
  mixedGeometry: [] as string[], longNames: [] as string[],
  collisions: [] as [string, string][], tooManyFields: false, over2gb: false, fieldCount: 3,
  estShpBytes: 10, estDbfBytes: 10, estPeakBytes: 10, overBrowserLimit: false,
  widthsEstimated: false, estReadBytes: 1000, rowGroups: 12, minClipBytes: 1000,
  rowCount: 7_000,
};
const MANGLED = { ...CLEAN, mixedGeometry: ["POINT", "LINESTRING"] };
// Truncation mangles data but still produces a file, so forcing past it is the user's call.
const TRUNCATED = { ...CLEAN, longNames: ["metadata_publication_id"] };
// Past the tab's memory ceiling: GeoPackage runs in the same wasm instance, so it is no way out.
const TOO_BIG = { ...CLEAN, overBrowserLimit: true, estPeakBytes: 7.2 * 1024 ** 3 };
// Two Utah-wide row groups: a clip cannot narrow what the export has to read.
const SLOW_READ = {
  ...CLEAN, estReadBytes: 1_351_235_892, rowGroups: 2, minClipBytes: 1_100_000_000,
};

const show = () => render(
  <QueryClientProvider client={new QueryClient({ defaultOptions: { mutations: { retry: false } } })}>
    <DownloadsPanel item={item} />
  </QueryClientProvider>,
);

beforeEach(() => {
  exportItem.mockClear();
  cancelExport.mockClear();
  startRun.mockClear();
  endRun.mockClear();
  runs.reset();
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
    expect(exportItem).toHaveBeenCalledWith(HREF, "x", "gpkg", undefined, 4326, 7);
  });

  it("holds back a GeoPackage the tab cannot hold, instead of crashing", async () => {
    exportWarnings.mockResolvedValue(TOO_BIG);
    show();
    await userEvent.click(screen.getByLabelText("Download GeoPackage"));
    expect(await screen.findByText(/too big to convert in the browser/i)).toBeDefined();
    expect(exportItem).not.toHaveBeenCalled();
  });

  // The pre-flight is the slow part (it downloads the file), so a cancel during it has to count.
  it("cancels during the pre-flight, before the export can deliver", async () => {
    let release: (v: unknown) => void = () => {};
    exportWarnings.mockImplementation(() => new Promise((r) => { release = r; }));
    show();
    await userEvent.click(screen.getByLabelText("Download Shapefile (zip)"));
    await userEvent.click(await screen.findByText(/^Cancel \w+ export/));
    // By ticket, so cancelling one export cannot swallow another's file.
    expect(cancelExport).toHaveBeenCalledWith(7);
    release(CLEAN);
    expect(exportItem).not.toHaveBeenCalled();
  });

  it("hands the export the ticket taken before the pre-flight ran", async () => {
    show();
    await userEvent.click(screen.getByLabelText("Download GeoJSON"));
    expect(beginExport).toHaveBeenCalled();
    expect(exportItem).toHaveBeenCalledWith(HREF, "x", "geojson", undefined, 4326, 7);
  });

  it("returns focus to the button that raised the warning", async () => {
    exportWarnings.mockResolvedValue(MANGLED);
    show();
    const button = screen.getByLabelText("Download Shapefile (zip)");
    await userEvent.click(button);
    await screen.findByLabelText(/will mangle/i);
    await userEvent.click(screen.getByText("Cancel"));
    expect(document.activeElement).toBe(button);
  });

  // One Cancel must not swallow another run's file, so each cancels the ticket it started with.
  it("cancels the run it started, not whatever else is in flight", async () => {
    beginExport.mockReturnValueOnce(11).mockReturnValueOnce(12);
    let release: (v: unknown) => void = () => {};
    exportWarnings.mockImplementation(() => new Promise((r) => { release = r; }));
    show();

    await userEvent.click(screen.getByLabelText("Download GeoJSON"));
    await userEvent.click(await screen.findByText(/^Cancel \w+ export/));
    expect(cancelExport).toHaveBeenLastCalledWith(11);

    await userEvent.click(screen.getByLabelText("Download CSV (WKT)"));
    await userEvent.click(await screen.findByText(/^Cancel \w+ export/));
    expect(cancelExport).toHaveBeenLastCalledWith(12);
    expect(cancelExport).toHaveBeenCalledTimes(2);
    release(CLEAN);
  });

  // Measured through the real drivers: shapefile and FileGDB fail outright on mixed geometry,
  // GeoPackage and FlatGeobuf take it.
  it("says the conversion fails on mixed geometry, not that features get dropped", async () => {
    exportWarnings.mockResolvedValue(MANGLED);
    show();
    await userEvent.click(screen.getByLabelText("Download File Geodatabase (zip)"));
    const box = await screen.findByLabelText(/will mangle/i);
    expect(box.textContent).toContain("the conversion fails rather than dropping the others");
    expect(box.textContent).toContain("GeoPackage or FlatGeobuf");
  });

  it("names the format the user actually picked, not always the shapefile", async () => {
    exportWarnings.mockResolvedValue({ ...MANGLED });
    show();
    await userEvent.click(screen.getByLabelText("Download File Geodatabase (zip)"));
    expect(await screen.findByText(/File Geodatabase \(zip\) will mangle this data/)).toBeDefined();
  });

  // An estimate the user cannot tell is an estimate is worse than no estimate.
  it("says so when the sizes are approximate, even with nothing else wrong", async () => {
    exportWarnings.mockResolvedValue({ ...CLEAN, widthsEstimated: true });
    show();
    await userEvent.click(screen.getByLabelText("Download Shapefile (zip)"));
    expect(await screen.findByText(/Sizes are approximate/i)).toBeDefined();
  });

  // A slow read mangles nothing, so the heading must not say it does.
  it("calls a slow read slow, not mangling", async () => {
    exportWarnings.mockResolvedValue(SLOW_READ);
    show();
    await userEvent.click(screen.getByLabelText("Download GeoPackage"));
    expect(await screen.findByText("This export will be slow")).toBeDefined();
    expect(screen.queryByText(/will mangle this data/i)).toBeNull();
  });

  // Clipping only helps when the file is grouped finely enough for the AOI to skip groups.
  it("says clipping cannot help when one block is most of the file", async () => {
    exportWarnings.mockResolvedValue(SLOW_READ);
    show();
    await userEvent.click(screen.getByLabelText("Download Shapefile (zip)"));
    expect(await screen.findByText(/clipping cannot bring it much below/i)).toBeDefined();
  });

  // Forcing past a memory ceiling crashes the tab rather than producing a file.
  it("offers no way to force an export the tab cannot hold", async () => {
    exportWarnings.mockResolvedValue(TOO_BIG);
    show();
    await userEvent.click(screen.getByLabelText("Download Shapefile (zip)"));
    await screen.findByText(/too big for the browser/i);
    expect(screen.queryByText("Download anyway")).toBeNull();
  });

  it("still lets the user force past a warning that only mangles", async () => {
    exportWarnings.mockResolvedValue(TRUNCATED);
    show();
    await userEvent.click(screen.getByLabelText("Download Shapefile (zip)"));
    expect(await screen.findByText("Download anyway")).toBeDefined();
  });

  // Mixed geometry fails the conversion, so there is no file to force out of it.
  it("offers no way to force past mixed geometry", async () => {
    exportWarnings.mockResolvedValue(MANGLED);
    show();
    await userEvent.click(screen.getByLabelText("Download Shapefile (zip)"));
    await screen.findByLabelText(/will mangle/i);
    expect(screen.queryByText("Download anyway")).toBeNull();
    expect(screen.getByText("Use GeoPackage instead")).toBeDefined();
  });

  // b4e0b8e extended the check to FileGDB; the named remedy has to be reachable there too.
  it("offers GeoPackage for a FileGDB mixed-geometry warning", async () => {
    exportWarnings.mockResolvedValue(MANGLED);
    show();
    await userEvent.click(screen.getByLabelText("Download File Geodatabase (zip)"));
    await screen.findByLabelText(/will mangle/i);
    expect(screen.getByText("Use GeoPackage instead")).toBeDefined();
  });

  it("announces the warning and moves focus to it", async () => {
    exportWarnings.mockResolvedValue(MANGLED);
    show();
    await userEvent.click(screen.getByLabelText("Download Shapefile (zip)"));
    const box = await screen.findByLabelText(/will mangle/i);
    expect(document.activeElement).toBe(box);
    expect(box.getAttribute("aria-labelledby")).toBe("dl-warn-title");
  });

  // An inline ref arrow re-fires on every commit and yanks focus off whatever the user is typing in.
  it("keeps focus on the clip inputs the warning tells the user to use", async () => {
    exportWarnings.mockResolvedValue(MANGLED);
    show();
    await userEvent.click(screen.getByLabelText("Download Shapefile (zip)"));
    await screen.findByLabelText(/will mangle/i);
    await userEvent.click(screen.getByText("Projection & area"));
    const clip = screen.getByLabelText(/clip/i);
    await userEvent.click(clip);
    expect(document.activeElement).toBe(clip);
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
    exportWarnings.mockResolvedValue(TRUNCATED);
    show();
    await userEvent.click(screen.getByLabelText("Download Shapefile (zip)"));
    await userEvent.click(await screen.findByText("Download anyway"));
    expect(exportItem).toHaveBeenCalledWith(HREF, "x", "shp", undefined, 4326, 7);
    expect(exportWarnings).toHaveBeenCalledTimes(1);
  });

  it("offers GeoPackage as the way out, and drops the warning", async () => {
    // Faithful to the real call: the shapefile-only findings are empty for any other format.
    exportWarnings.mockImplementation((_u: string, fmt: string) =>
      Promise.resolve(fmt === "shp" ? MANGLED : CLEAN));
    show();
    await userEvent.click(screen.getByLabelText("Download Shapefile (zip)"));
    await userEvent.click(await screen.findByText("Use GeoPackage instead"));
    expect(exportItem).toHaveBeenCalledWith(HREF, "x", "gpkg", undefined, 4326, 7);
    expect(screen.queryByText(/will mangle this data/i)).toBeNull();
  });

  it("proceeds when the pre-flight itself fails", async () => {
    exportWarnings.mockRejectedValue(new Error("read failed"));
    show();
    await userEvent.click(screen.getByLabelText("Download Shapefile (zip)"));
    expect(exportItem).toHaveBeenCalledWith(HREF, "x", "shp", undefined, 4326, 7);
  });

  it("reports a failed export instead of failing silently", async () => {
    exportItem.mockRejectedValueOnce(new Error("gdal exploded"));
    show();
    await userEvent.click(screen.getByLabelText("Download GeoJSON"));
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", "Download failed: gdal exploded");
  });
});
