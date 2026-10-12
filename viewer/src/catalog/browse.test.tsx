// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { CollectionItems, CollectionsGrid, type CollectionSummary } from "./browse";
import { ItemDetail } from "./item-detail";

const card = (id: string, title: string): CollectionSummary =>
  ({ id, title, href: `https://cdn.example/stac/${id}/collection.json`, count: 1 });

const CARDS = [
  card("ugs-serving-topics", "Serving Topics"),
  card("ugs-rasters", "Rasters"),
  card("ugs-publications", "Publications"),
  card("ugs-external", "External"),
  card("https://ubm-assets.geology.utah.gov/stac/catalog.json", "UGS Soil Water Balance"),
  card("ugs-flux", "Flux"),                       // a root nobody has classified yet
];

const noop = () => {};
const atRoot = (cards: CollectionSummary[]) => render(
  <CollectionsGrid cards={cards} allItems={[]} itemsLoading={false} atRoot
    breadcrumb={[]} search="" onSearch={noop} threeD={false} onThreeD={noop}
    browseAll={false} onBrowseAll={noop} layerCollectionIds={[]} series={[]} onSeries={noop}
    onOpenCollection={vi.fn()} onOpenItem={noop} onOpenCover={noop} />,
);

describe("the catalog landing", () => {
  it("groups the root by kind instead of one mixed grid", () => {
    atRoot(CARDS);
    for (const heading of ["Map layers", "Publications & records", "Other UGS catalogs"]) {
      expect(screen.getByText(heading), heading).toBeDefined();
    }
  });

  // The alarm from catalog.ts, at the render level: an unclassified root is visible, not filed
  // under publications where nobody would question it.
  it("shows an unknown root under its own heading", () => {
    atRoot(CARDS);
    expect(screen.getByText("Everything else")).toBeDefined();
  });

  it("leaves out a group with nothing in it", () => {
    atRoot(CARDS.filter((c) => c.id === "ugs-publications"));
    expect(screen.queryByText("Map layers")).toBeNull();
    expect(screen.queryByText("Everything else")).toBeNull();
    expect(screen.getByText("Publications & records")).toBeDefined();
  });

  it("gives every collection its own STAC url to hand out", () => {
    atRoot([card("ugs-publications", "Publications")]);
    expect(screen.getByText("STAC ↗").getAttribute("href")).toBe("https://cdn.example/stac/ugs-publications/collection.json");
  });
});

describe("an item that is not in the catalog", () => {
  it("says so, with a way back to its collection, instead of loading forever", () => {
    const onBack = vi.fn();
    render(<ItemDetail collectionId="ugs-mining-district-files/MD" missing="MD-1002" layout="page"
      onBack={onBack} onMap={noop} />);
    expect(screen.getByRole("status").textContent).toContain("Not found: MD-1002 is not in this catalog.");
    screen.getByRole("button", { name: /Back to MD/ }).click();
    expect(onBack).toHaveBeenCalledOnce();
    expect(screen.queryByText("Loading…")).toBeNull();
  });
});

describe("a series list", () => {
  it("says a catalog has no item index instead of showing an empty list", () => {
    render(<CollectionItems items={[]} itemsLoading={false} noIndex breadcrumb={[{ label: "Catalog" }]}
      series={[]} onOpenItem={noop} onSeries={noop} />);
    expect(screen.getByRole("status").textContent).toBe("This catalog has no item index.");
  });
});
