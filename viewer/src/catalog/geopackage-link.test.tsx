// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import type { StacDoc } from "@/stac";
import { GeopackageLink, PublicationParts } from "./item-detail";

// The parts list links with the router's <Link>; a plain anchor is enough to render it.
vi.mock("@tanstack/react-router", async (orig) => ({
  ...(await orig<typeof import("@tanstack/react-router")>()),
  Link: ({ children }: { children: ReactNode }) => <a href="#part">{children}</a>,
}));

const GPKG = "https://x/geolmap/vectors/M-228DM/M-228DM.gpkg";
const pub = (assets: StacDoc["assets"]): StacDoc => ({
  id: "M-228DM", type: "Collection",
  assets,
  links: [{ rel: "item", href: "./M-228DM__Polys/M-228DM__Polys.json", title: "Polys" } as never],
});
const gpkg = { href: GPKG, type: "application/geopackage+sqlite3", roles: ["data"],
  title: "All data (GeoPackage)", "file:size": 530_400_000 };

describe("the GeoPackage download on a publication page", () => {
  it("links the file with its size", () => {
    render(<GeopackageLink item={pub({ geopackage: gpkg })} />);
    const link = screen.getByRole("link", { name: "Download all data (GeoPackage, 530 MB)" });
    expect(link.getAttribute("href")).toBe(GPKG);
  });

  it("omits the size when the asset has none", () => {
    render(<GeopackageLink item={pub({ geopackage: { ...gpkg, "file:size": undefined } })} />);
    expect(screen.getByRole("link", { name: "Download all data (GeoPackage)" })).toBeTruthy();
  });

  it("is absent when the publication has no GeoPackage asset", () => {
    render(<GeopackageLink item={pub({})} />);
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("sits under the Layers and tables heading", () => {
    render(<PublicationParts item={pub({ geopackage: gpkg })} collectionId="ugs-publications/M" />);
    const heading = screen.getByRole("heading", { name: "Layers and tables" });
    const link = screen.getByRole("link", { name: /Download all data/ });
    expect(heading.compareDocumentPosition(link) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});
