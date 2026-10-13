// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { FeatureCard } from "@/map/feature-card";
import { PreviewMapProvider } from "@/map/preview-map";
import type { StacDoc } from "@/stac";
import { RelatedPanel } from "./item-detail";

// The related-row read a click makes (hyparquet, an exact match on the key column).
const { readMatching } = vi.hoisted(() => ({ readMatching: vi.fn() }));
vi.mock("@/data/parquet-lite", () => ({ readMatching }));

const DMU = "https://x/vectors/OFR-784DM/OFR784DM__DescriptionOfMapUnits.parquet";
const SOURCES = "https://x/vectors/OFR-784DM/OFR784DM__DataSources.parquet";
const ORIGIN = "https://x/vectors/OFR-784DM/OFR784DM__MapUnitPolys.parquet";
const fk = (fields: string[], refFields: string[], how: Record<string, unknown> = {}) =>
  ({ fields, reference: { resource: "OFR-784DM_OFR784DM__MapUnitPolys", href: ORIGIN, fields: refFields }, ...how });

// A publication's MapUnitPolys layer: its related tables in the serving topics' flipped shape, with
// how each key was found. DataSources is a reference table (most of the pub's layers point at it).
const mapUnitPolys: StacDoc = {
  type: "Feature", id: "OFR-784DM_OFR784DM__MapUnitPolys",
  properties: { title: "Map Unit Polys", "ugs:layer_kind": "layer", "ugs:source_dataset": "OFR784DM Mills Junction" },
  assets: {
    data: { href: ORIGIN, type: "application/vnd.apache.parquet", roles: ["data"],
            "ugs:foreign_keys": [{ fields: ["MapUnit"], reference: { resource: "OFR-784DM_OFR784DM__DescriptionOfMapUnits", href: DMU, fields: ["MapUnit"] },
                                    "ugs:detected_by": "inferred", "ugs:confidence": 100 }] },
    OFR784DM__DescriptionOfMapUnits: { href: DMU, type: "application/vnd.apache.parquet", roles: ["data", "related"],
      title: "Description Of Map Units", "ugs:foreign_keys": [fk(["MapUnit"], ["MapUnit"], { "ugs:detected_by": "inferred", "ugs:confidence": 100 })] },
    OFR784DM__DataSources: { href: SOURCES, type: "application/vnd.apache.parquet", roles: ["data", "related", "reference"],
      title: "Data Sources", "ugs:foreign_keys": [fk(["DataSources_ID"], ["DataSourceID"], { "ugs:detected_by": "declared" })] },
  },
  links: [],
};

const wrap = (ui: React.ReactNode) => render(
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <PreviewMapProvider>{ui}</PreviewMapProvider>
  </QueryClientProvider>,
);

beforeEach(() => readMatching.mockReset());

describe("a map click on a publication layer", () => {
  // The tile's properties: no feature_id, just the layer's columns, MapUnit among them.
  const props = { MapUnit: "Qal", DataSourceID: "DAS1", Shape_Area: 12.5 };

  it("shows the joined DMU row through the key value, with no feature_id", async () => {
    readMatching.mockImplementation(async (url: string, col: string, value: string) => (url === DMU
      ? { total: 1, columns: ["MapUnit", "Name", "Age", "Description"],
          rows: [{ MapUnit: value, Name: "Alluvium", Age: "Holocene", Description: "Stream gravel" }] }
      : { total: 1, columns: [col], rows: [{ [col]: value }] }));
    wrap(<FeatureCard item={mapUnitPolys} props={props} />);
    expect(await screen.findByText("Alluvium")).toBeDefined();
    expect(screen.getByText("Holocene")).toBeDefined();
    expect(readMatching).toHaveBeenCalledWith(DMU, "MapUnit", "Qal", expect.anything());
  });

  it("lists the reference table after the join, collapsed, and reads it only once opened", async () => {
    readMatching.mockImplementation(async (url: string) => (url === SOURCES
      ? { total: 1, columns: ["DataSources_ID", "Source"], rows: [{ DataSources_ID: "DAS1", Source: "Field mapping" }] }
      : { total: 1, columns: ["MapUnit", "Name"], rows: [{ MapUnit: "Qal", Name: "Alluvium" }] }));
    wrap(<FeatureCard item={mapUnitPolys} props={props} />);
    const toggle = screen.getByRole("button", { name: /References: Data Sources/ });
    await screen.findByText("Alluvium");
    expect(screen.queryByText("Field mapping")).toBeNull();
    expect(readMatching).not.toHaveBeenCalledWith(SOURCES, expect.anything(), expect.anything(), expect.anything());
    await userEvent.click(toggle);
    expect(await screen.findByText("Field mapping")).toBeDefined();
    expect(readMatching).toHaveBeenCalledWith(SOURCES, "DataSources_ID", "DAS1", expect.anything());
  });
});

describe("RelatedPanel on a publication layer", () => {
  it("marks the inferred join subtly, with its confidence, and the declared one not at all", () => {
    wrap(<RelatedPanel item={mapUnitPolys} />);
    const marks = screen.getAllByText("inferred");
    // The DMU table's key and the layer's own outgoing key are both inferred; DataSources is declared.
    expect(marks).toHaveLength(2);
    expect(marks[0].getAttribute("title")).toContain("confidence 100 of 100");
  });

  it("puts the reference table after the primary ones, collapsed", () => {
    const { container } = wrap(<RelatedPanel item={mapUnitPolys} />);
    expect(screen.getByText("Related tables")).toBeDefined();
    const refs = container.querySelector("details") as HTMLDetailsElement;
    expect(refs.textContent).toContain("Reference tables: Data Sources");
    expect(refs.open).toBe(false);
    // The primary DMU table is listed outside the collapsed section.
    const primary = screen.getByText("Description Of Map Units");
    expect(refs.contains(primary)).toBe(false);
  });
});
