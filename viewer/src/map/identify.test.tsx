// @vitest-environment jsdom
import { act, render, screen } from "@testing-library/react";
import { createMemoryHistory, createRootRoute, createRoute, createRouter, RouterProvider } from "@tanstack/react-router";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { toLayer } from "@/app";
import { type Asset, rootIndexItems, type StacDoc } from "@/stac";
import { descriptionsPdfOf, identifyAt, identifyClick, type IdentifyTarget, pubContext, topUnit, unitsHrefOf } from "./identify";
import { IdentifyLayer } from "./identify-layer";
import { orderedSublayerIds } from "./map-model";
import { UnitCard } from "./unit-card";

// react-map-gl needs a live map; the stand-ins record what the layer asks for.
vi.mock("react-map-gl/maplibre", () => ({
  Source: ({ id, url, children }: { id: string; url: string; children: React.ReactNode }) =>
    <div data-testid="source" data-id={id} data-url={url}>{children}</div>,
  Layer: (p: Record<string, unknown>) =>
    <div data-testid="layer" data-id={String(p.id)} data-type={String(p.type)}
      data-source-layer={String(p["source-layer"])} data-opacity={String((p.paint as Record<string, unknown>)["fill-opacity"])} />,
}));

const UNITS = { href: "https://x/units-100k.pmtiles", type: "application/vnd.pmtiles", roles: ["data"], title: "Geologic units (identify)" };
const COG = { href: "https://x/p.cog.tif", type: "image/tiff; application=geotiff; profile=cloud-optimized", roles: ["data", "cloud-optimized"] };

const plateWith = (assets: Record<string, Asset>) => rootIndexItems({ items: [
  { id: "M-1_plate", bbox: [-113, 37, -109, 41], properties: { title: "Map plate", "ugs:in_publication": "M-1", "ugs:publication_title": "Geology of Somewhere" },
    assets, links: [{ rel: "self", href: "./ugs-publications/M/M-1/M-1_plate/M-1_plate.json" }] },
] })[0];

describe("the identify layer follows the units asset", () => {
  it("is on the layer only when the Item has a units asset", () => {
    expect(toLayer(plateWith({ cog: COG, units: UNITS }))).toMatchObject({ cogHref: COG.href, unitsHref: UNITS.href });
    expect(toLayer(plateWith({ cog: COG }))?.unitsHref).toBeUndefined();
    expect(unitsHrefOf({ assets: { units: { href: "https://x/u.parquet", type: "application/vnd.apache.parquet" } } } as StacDoc)).toBeUndefined();
  });

  it("draws as an invisible fill on the units tile layer", () => {
    render(<IdentifyLayer slug="a" href={UNITS.href} />);
    expect(screen.getByTestId("source").dataset).toMatchObject({ id: "idn-a", url: `pmtiles://${UNITS.href}` });
    expect(screen.getByTestId("layer").dataset).toMatchObject({ id: "idn-a-fill", type: "fill", sourceLayer: "units", opacity: "0.001" });
  });

  it("is ordered with its raster, and gone when the raster is", () => {
    const withUnits = { id: "a", title: "A", cogHref: COG.href, unitsHref: UNITS.href };
    const without = { id: "b", title: "B", rasterPmHref: "https://x/m.pmtiles" };
    const opts = { styledCount: () => undefined, cogReady: true };
    expect(orderedSublayerIds([without, withUnits], opts)).toEqual(["rpm-b-raster", "cog-a-raster", "idn-a-fill"]);
    expect(orderedSublayerIds([without], opts)).toEqual(["rpm-b-raster"]);
  });
});

// A map whose identify layer returns the given features.
const mapOf = (features: { properties: Record<string, unknown> }[], has = true) => ({
  getLayer: () => (has ? {} : undefined),
  queryRenderedFeatures: vi.fn(() => features),
});
const feat = (rank: number, code: string) => ({ properties: { series_id: "M-1", unit_code: code, unit_name: `Name ${code}`, age: "Jurassic", description: "d", rank } });
const target: IdentifyTarget = { slug: "a", hasUnits: true, bbox: [-113, 37, -109, 41], pubTitle: "Geology of Somewhere" };
const click = { point: { x: 10, y: 20 }, lngLat: { lng: -111, lat: 39 } };

describe("identifyAt", () => {
  it("takes the highest rank where quads overlap", () => {
    expect(topUnit([feat(3, "Jm"), feat(9, "Qa"), feat(5, "Tv")])?.code).toBe("Qa");
    const m = mapOf([feat(1, "Jm"), feat(2, "Qa")]);
    expect(identifyAt(m, click.point, click.lngLat, [target])).toMatchObject({ kind: "unit", unit: { code: "Qa", rank: 2 } });
    expect(m.queryRenderedFeatures).toHaveBeenCalledWith([10, 20], { layers: ["idn-a-fill"] });
  });

  it("says scan only on the footprint with no unit under it, and nothing outside it", () => {
    expect(identifyAt(mapOf([]), click.point, click.lngLat, [target]))
      .toEqual({ kind: "scan-only", pubTitle: "Geology of Somewhere" });
    expect(identifyAt(mapOf([]), click.point, { lng: -100, lat: 39 }, [target])).toBeNull();
  });

  it("does not query a raster that has no identify layer", () => {
    const m = mapOf([feat(1, "Jm")]);
    expect(identifyAt(m, click.point, click.lngLat, [{ ...target, hasUnits: false }])).toMatchObject({ kind: "scan-only" });
    expect(m.queryRenderedFeatures).not.toHaveBeenCalled();
    expect(identifyAt(mapOf([feat(1, "Jm")], false), click.point, click.lngLat, [target])).toMatchObject({ kind: "scan-only" });
  });

  it("asks the front-most raster first", () => {
    const m = { getLayer: () => ({}), queryRenderedFeatures: vi.fn((_g: unknown, o: { layers: string[] }) => (o.layers[0] === "idn-b-fill" ? [feat(1, "Front")] : [feat(1, "Back")])) };
    const r = identifyAt(m, click.point, click.lngLat, [{ ...target, slug: "b" }, target]);
    expect(r).toMatchObject({ unit: { code: "Front" } });
  });
});

describe("identifyClick", () => {
  it("lets a coverage popup win over a scan-only answer, not over a unit", () => {
    const cov = { layer: { id: "coverage-fill" } };
    expect(identifyClick(cov, () => ({ kind: "scan-only" }))).toBeNull();
    const unit = { kind: "unit", unit: { seriesId: "M-1", code: "Jm", rank: 1 } } as const;
    expect(identifyClick(cov, () => unit)).toBe(unit);
  });
  const scan = { kind: "scan-only" } as const;
  it("leaves a click on a vector feature alone", () => {
    const run = vi.fn(() => scan);
    expect(identifyClick({ layer: { id: "pm-x-fill" } }, run)).toBeNull();
    expect(run).not.toHaveBeenCalled();
  });
  it("identifies on the bare map", () => {
    expect(identifyClick(undefined, () => scan)).toBe(scan);
  });
});

const entries = rootIndexItems({ items: [
  { id: "M-1", bbox: [-113, 37, -109, 41], properties: { title: "Geology of Somewhere" }, links: [{ rel: "self", href: "./ugs-publications/M/M-1/collection.json" }] },
  { id: "M-1__DMU", properties: { title: "Description of Map Units", "ugs:layer_kind": "table", "ugs:in_publication": "M-1", "ugs:publication_title": "Geology of Somewhere" },
    links: [{ rel: "self", href: "./ugs-publications/M/M-1/M-1__DMU/M-1__DMU.json" }] },
  { id: "M-2__DMU", properties: { title: "DescriptionOfMapUnits", "ugs:layer_kind": "table", "ugs:in_publication": "M-2", "ugs:publication_title": "Other" },
    links: [{ rel: "self", href: "./ugs-publications/M/M-2/M-2__DMU/M-2__DMU.json" }] },
  { id: "M-3__Poly", properties: { title: "Polys", "ugs:layer_kind": "layer", "ugs:in_publication": "M-3", "ugs:publication_title": "No table" },
    links: [{ rel: "self", href: "./ugs-publications/M/M-3/M-3__Poly/M-3__Poly.json" }] },
  { id: "M-4", properties: { title: "Coverage-era map" }, links: [{ rel: "self", href: "./ugs-publications/M/M-4/collection.json" }],
    assets: { publication: { href: "https://pubs.example/M-4.pdf", type: "application/pdf", title: "Publication" },
              booklet_pdf: { href: "https://pubs.example/M-4-booklet.pdf", type: "application/pdf", title: "Booklet - PDF" } } },
] });

describe("pubContext", () => {
  it("resolves the title, the page and the unit-descriptions table", () => {
    const c = pubContext("M-1", entries);
    expect(c.title).toBe("Geology of Somewhere");
    expect(c.page).toEqual({ c: "ugs-publications/M", i: "M-1" });
    expect(c.dmu).toEqual({ c: expect.stringContaining("M-1"), i: "M-1__DMU" });
  });

  it("matches the table title ignoring spaces and case, and only a table", () => {
    expect(pubContext("M-2", entries).dmu?.i).toBe("M-2__DMU");
    expect(pubContext("M-3", entries).dmu).toBeUndefined();
  });

  it("falls back to the series id", () => {
    expect(pubContext("ZZ-9", entries)).toEqual({ title: "ZZ-9", page: undefined, dmu: undefined, pdf: undefined });
  });

  it("links the booklet when the publication has one, else the publication PDF", () => {
    expect(pubContext("M-4", entries).pdf).toEqual({ href: "https://pubs.example/M-4-booklet.pdf", label: "Booklet" });
    expect(descriptionsPdfOf({ assets: { publication: { href: "https://pubs.example/p.pdf", type: "application/pdf", title: "Publication" },
      plate_1_pdf: { href: "https://pubs.example/plate.pdf", type: "application/pdf", title: "Plate 1 - PDF" } } }))
      .toEqual({ href: "https://pubs.example/p.pdf", label: "Publication" });
    expect(descriptionsPdfOf({ assets: { zip: { href: "https://pubs.example/g.zip", type: "application/zip" } } })).toBeUndefined();
    expect(descriptionsPdfOf({ assets: { file: { href: "https://pubs.example/untyped.pdf", title: "" } } }))
      .toEqual({ href: "https://pubs.example/untyped.pdf", label: "Publication" });
  });
});

// The card links with the router's <Link>, so it renders inside one.
async function renderCard(ui: React.ReactElement) {
  const root = createRootRoute({ component: () => ui });
  const catalog = createRoute({ getParentRoute: () => root, path: "/catalog", component: () => null });
  const router = createRouter({ routeTree: root.addChildren([catalog]), history: createMemoryHistory({ initialEntries: ["/"] }) });
  await act(async () => { render(<RouterProvider router={router} />); });
  return router;
}

describe("UnitCard", () => {
  const long = "x".repeat(300);
  const unit = { seriesId: "M-1", code: "Jm", name: "Morrison Formation", age: "Late Jurassic", description: long, rank: 1 };

  it("shows code, name, age, a clamped description, and the publication links", async () => {
    await renderCard(<UnitCard result={{ kind: "unit", unit }} pub={pubContext("M-1", entries)} />);
    expect(screen.getByText("Jm").className).toContain("font-bold");
    expect(screen.getByText(/Morrison Formation/)).toBeTruthy();
    expect(screen.getByText(/Late Jurassic/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Geology of Somewhere" }).getAttribute("href")).toMatch(/^\/catalog\?c=.*&i=M-1$/);
    expect(screen.getByRole("link", { name: "View unit descriptions →" }).getAttribute("href")).toContain("i=M-1__DMU");
    await userEvent.click(screen.getByRole("button", { name: "more" }));
    expect(screen.getByRole("button", { name: "less" })).toBeTruthy();
  });

  it("names the series id when the publication is unknown, with no table link", async () => {
    await renderCard(<UnitCard result={{ kind: "unit", unit }} pub={pubContext("ZZ-9", entries)} />);
    expect(screen.getByText("ZZ-9")).toBeTruthy();
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("says where the descriptions are when the GIS data has none", async () => {
    const bare = { seriesId: "M-4", code: "Qal", name: "Alluvium", rank: 1 };
    await renderCard(<UnitCard result={{ kind: "unit", unit: bare }} pub={pubContext("M-4", entries)} />);
    expect(screen.getByText(/description is not recorded in the map's GIS data/)).toBeTruthy();
    const pdf = screen.getByRole("link", { name: "Booklet (PDF) ↗" });
    expect(pdf.getAttribute("href")).toBe("https://pubs.example/M-4-booklet.pdf");
    expect(pdf.getAttribute("target")).toBe("_blank");
  });

  it("leaves the note out when the unit has a description or a descriptions table", async () => {
    await renderCard(<UnitCard result={{ kind: "unit", unit: { ...unit, description: undefined } }} pub={pubContext("M-1", entries)} />);
    expect(screen.queryByText(/not recorded in the map's GIS data/)).toBeNull();
  });

  it("waits for the publication before saying the description is missing", async () => {
    const bare = { seriesId: "M-4", code: "Qal", rank: 1 };
    await renderCard(<UnitCard result={{ kind: "unit", unit: bare }} />);
    expect(screen.queryByText(/not recorded in the map's GIS data/)).toBeNull();
  });

  it("shows the scan-only notice with the covering publication", async () => {
    await renderCard(<UnitCard result={{ kind: "scan-only", pubTitle: "Geology of Somewhere" }} />);
    expect(screen.getByText("No unit data for this map (scan only)")).toBeTruthy();
    expect(screen.getByText("Geology of Somewhere")).toBeTruthy();
  });

  it("moves focus to the heading when it opens, and Escape dismisses it", async () => {
    const onClose = vi.fn();
    await renderCard(<UnitCard result={{ kind: "scan-only" }} onClose={onClose} />);
    expect(screen.getByRole("region", { name: "Geologic unit" })).toBeTruthy();
    expect(document.activeElement).toBe(screen.getByText("Geologic unit"));
    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("ignores Escape typed into a field", async () => {
    const onClose = vi.fn();
    await renderCard(<><input aria-label="q" /><UnitCard result={{ kind: "scan-only" }} onClose={onClose} /></>);
    await userEvent.click(screen.getByLabelText("q"));
    await userEvent.keyboard("{Escape}");
    expect(onClose).not.toHaveBeenCalled();
  });
});
