// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AddAllLayersButton } from "@/map/add-all-layers-button";
import type { StacDoc } from "@/stac";
import { PartStepper } from "./item-detail";

const ctx = vi.hoisted(() => ({
  layerIds: [] as string[],
  seriesSel: ["OFR"] as string[] | undefined,
  isActive: (_id: string): boolean => false,
  toggleLayers: vi.fn(),
  go: vi.fn(),
}));
vi.mock("@/app", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/app")>()),
  useViewCtx: () => ctx,
}));
const open = vi.hoisted(() => vi.fn());
vi.mock("./entry-link", () => ({
  EntryLink: ({ c, i, children }: { c: string; i: string; children: ReactNode }) => <a href={`?c=${c}&i=${i}`}>{children}</a>,
  useOpenEntry: () => open,
}));

const part = (name: string, kind: string, geometry?: string) => ({
  rel: "item", href: `./OFR-784DM__Mills__${name}/OFR-784DM__Mills__${name}.json`, title: name,
  "ugs:layer_kind": kind, ...(geometry ? { "ugs:geometry_kind": geometry } : {}),
});
const pub: StacDoc = {
  type: "Collection", id: "OFR-784DM", bbox: [-112.5, 39.5, -112.375, 39.625],
  links: [
    { rel: "item", href: "./OFR-784DM_plate/OFR-784DM_plate.json", title: "Map plate" },
    part("MapUnitPolys", "layer", "polygons"),
    part("ContactsAndFaults", "layer", "lines"),
    part("OrientationPoints", "layer", "points"),
    part("Glossary", "table"),
    part("DataSources", "table"),
  ],
};
afterEach(cleanup);
const ids = (...names: string[]) => names.map((n) => `OFR-784DM__Mills__${n}`);

describe("Add all map layers", () => {
  beforeEach(() => { ctx.isActive = () => false; ctx.toggleLayers.mockReset(); ctx.go.mockReset(); });

  it("adds every map layer in one call, points first, and leaves tables and the plate out", async () => {
    render(<AddAllLayersButton item={pub} />);
    await userEvent.click(screen.getByRole("button", { name: "Add all 3 map layers to the Map" }));
    expect(ctx.toggleLayers).toHaveBeenCalledWith(ids("OrientationPoints", "ContactsAndFaults", "MapUnitPolys"), true);
  });

  it("once all are on, removes them, and Open the Map keeps the series filter", async () => {
    ctx.isActive = () => true;
    render(<AddAllLayersButton item={pub} />);
    await userEvent.click(screen.getByRole("button", { name: /All 3 map layers on the Map/ }));
    expect(ctx.toggleLayers).toHaveBeenCalledWith(expect.any(Array), false);
    await userEvent.click(screen.getByRole("button", { name: "Open the Map ›" }));
    expect(ctx.go).toHaveBeenCalledWith({ view: "map", l: ctx.layerIds, s: ["OFR"] });
  });

  it("is not offered for a publication with fewer than two map layers", () => {
    const { container } = render(<AddAllLayersButton item={{ ...pub, links: [part("MapUnitPolys", "layer", "polygons")] }} />);
    expect(container.innerHTML).toBe("");
  });
});

describe("stepping through a publication's layers", () => {
  const partOf = { title: "Mills Junction", c: "ugs-publications/OFR", i: "OFR-784DM", doc: pub };
  const c = "ugs-publications/OFR/OFR-784DM";

  it("says where this layer sits and links its neighbours under the publication", () => {
    render(<PartStepper partOf={partOf} id={ids("ContactsAndFaults")[0]} />);
    screen.getByText("Layer 2 of 3");
    expect(screen.getByRole("link", { name: "‹ Previous" }).getAttribute("href")).toBe(`?c=${c}&i=${ids("MapUnitPolys")[0]}`);
    expect(screen.getByRole("link", { name: "Next ›" }).getAttribute("href")).toBe(`?c=${c}&i=${ids("OrientationPoints")[0]}`);
    screen.getByRole("combobox", { name: "Jump to another layer" });
  });

  it("has no previous link on the first layer, and steps tables among tables", () => {
    render(<PartStepper partOf={partOf} id={ids("MapUnitPolys")[0]} />);
    expect(screen.queryByRole("link", { name: "‹ Previous" })).toBeNull();
    cleanup();
    render(<PartStepper partOf={partOf} id={ids("Glossary")[0]} />);
    screen.getByText("Table 1 of 2");
  });

  it("is not shown on a page that is not part of a publication, or for the plate", () => {
    const { container } = render(<PartStepper id={ids("MapUnitPolys")[0]} />);
    expect(container.innerHTML).toBe("");
    cleanup();
    const plate = render(<PartStepper partOf={partOf} id="OFR-784DM_plate" />);
    expect(plate.container.innerHTML).toBe("");
  });
});
