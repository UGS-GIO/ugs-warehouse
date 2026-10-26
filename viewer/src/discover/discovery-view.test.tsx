// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { DiscoveryView } from "./discovery-view";

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children }: { children: React.ReactNode }) => <a href="/">{children}</a>,
  useNavigate: () => vi.fn(),
  useSearch: () => ({}),
}));
vi.mock("./open-map-pill", () => ({ OpenMapPill: () => null }));
vi.mock("@/map/add-to-map-button", () => ({ AddToMapButton: () => null }));
// The real ItemDetail needs the app context; this stand-in renders only the trail it was handed.
vi.mock("@/catalog/item-detail", async () => {
  const { Breadcrumb } = await import("@/catalog/breadcrumb");
  return { ItemDetail: ({ crumbs }: { crumbs?: { label: string; onClick?: () => void }[] }) =>
    <Breadcrumb crumbs={crumbs ?? []} /> };
});

describe("the Discover drawer", () => {
  it("hands the selected item's trail to the detail, so a crumb navigates and never closes the drawer", () => {
    const onCloseItem = vi.fn();
    const onSeries = vi.fn();
    render(<QueryClientProvider client={new QueryClient()}>
      <DiscoveryView items={[]} itemsKey="k" onOpenItem={vi.fn()} onOpenPub={vi.fn()} itemSelected
        selectedItem={{ type: "Feature", id: "OFR-9", properties: {} } as never}
        selectedCollectionId="ugs-publications/OFR"
        selectedCrumbs={[{ label: "Catalog" }, { label: "Open File Report", onClick: onSeries }, { label: "Map X" }]}
        onCloseItem={onCloseItem} onViewOnMap={vi.fn()} />
    </QueryClientProvider>);
    screen.getByRole("button", { name: "Open File Report" }).click();
    expect(onSeries).toHaveBeenCalledOnce();
    expect(onCloseItem).not.toHaveBeenCalled();
  });
});
