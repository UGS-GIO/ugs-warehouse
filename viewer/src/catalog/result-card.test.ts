import { describe, expect, it } from "vitest";

import type { ItemRef } from "@/catalog/browse";
import { itemLink } from "@/catalog/result-card";
import { formatBadgeAssets } from "@/catalog/item-view";

const it_: ItemRef = {
  collId: "ugs-serving-topics/emp",
  href: "https://cdn.example/stac/emp/enmin_ccs_natcarb_location/enmin_ccs_natcarb_location.json",
} as ItemRef;

const apply = (search: ReturnType<typeof itemLink>["search"], prev: Record<string, unknown>) =>
  typeof search === "function" ? search(prev) : search;

describe("itemLink", () => {
  // The regression this pins: <Link search={obj}> REPLACES the search, so opening a card from a
  // filtered set silently cleared category/q/sort — the list jumped from 21 results back to 7620.
  it("keeps the Discover filters when opening the drawer", () => {
    const link = itemLink(it_, true);
    expect(link.to).toBe("/discover");
    expect(apply(link.search, { category: "energy-minerals", q: "wells", sort: "newest" }))
      .toEqual({
        category: "energy-minerals", q: "wells", sort: "newest",
        c: "ugs-serving-topics/emp", i: "enmin_ccs_natcarb_location",
      });
  });

  // Leaving Discover is the one case where dropping them is right — they do not apply on the
  // full page, and carrying them there would resurrect a filter the user cannot see or clear.
  it("drops them when leaving for the full page", () => {
    const link = itemLink(it_, false);
    expect(link.to).toBe("/catalog");
    expect(apply(link.search, { category: "energy-minerals", q: "wells" }))
      .toEqual({ c: "ugs-serving-topics/emp", i: "enmin_ccs_natcarb_location" });
  });
});

describe("formatBadgeAssets", () => {
  it("leaves the cover, images and the ISO 19139 metadata record off the format chips", () => {
    const pub: ItemRef = { collId: "ugs-publications/M", href: "https://cdn.example/stac/M/M-1/collection.json",
      data: { id: "M-1", assets: {
        publication: { href: "m.pdf", type: "application/pdf", title: "Publication" },
        thumbnail: { href: "c.webp", type: "image/webp", roles: ["thumbnail"] },
        overview: { href: "o.png", type: "image/png" },
        metadata: { href: "m.xml", type: "application/xml", roles: ["metadata"], title: "ISO 19139 metadata" },
      } } };
    expect(formatBadgeAssets(pub).map(([k]) => k)).toEqual(["publication"]);
  });
});
