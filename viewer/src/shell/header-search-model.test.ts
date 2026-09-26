import { describe, expect, it } from "vitest";
import type { ItemRef } from "@/catalog/browse";
import { buildCatalogSearch } from "./header-search-model";

const item = (collId: string, id: string, props: Record<string, unknown>, bbox?: number[]): ItemRef => ({
  collId, href: `https://x/stac/${collId}/${id}/${id}.json`, data: { id, bbox, properties: props },
});

const ITEMS = [
  item("hazards", "hazards_qfaults", { title: "Quaternary Faults and Folds", "ugs:topic": "Hazards" }, [-114, 37, -109, 42]),
  item("DS", "DS-7", { title: "2025 Update to the Utah Quaternary Fault Database", "ugs:pub_type": "Data Series", datetime: "2026-01-01" }),
  item("OFR", "OFR-598", { title: "Progress report geologic map of the Grouse Creek quadrangle", "ugs:pub_type": "Open File Report", datetime: "2012-06-01" }, [-114.08, 41.48, -112.97, 42.02]),
];
const isLayer = (r: ItemRef) => r.collId === "hazards";

describe("buildCatalogSearch", () => {
  it("splits hits into layers and publications", async () => {
    const res = (await buildCatalogSearch(ITEMS, isLayer))("fault");
    expect(res.layers.map((h) => h.id)).toEqual(["hazards_qfaults"]);
    expect(res.publications.map((h) => h.id)).toEqual(["DS-7"]);
    expect(res.publications[0].sub).toBe("DS-7 · Data Series · 2026");
  });

  it("returns a series ID as the exact match, in any case", async () => {
    const res = (await buildCatalogSearch(ITEMS, isLayer))("ofr-598");
    expect(res.exact?.id).toBe("OFR-598");
    expect(res.exact?.bbox).toEqual([-114.08, 41.48, -112.97, 42.02]);
    expect(res.publications.map((h) => h.id)).not.toContain("OFR-598");
  });

  it("returns nothing for one character", async () => {
    const res = (await buildCatalogSearch(ITEMS, isLayer))("f");
    expect(res).toEqual({ layers: [], publications: [] });
  });
});
