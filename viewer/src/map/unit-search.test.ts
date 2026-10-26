import { describe, expect, it, vi } from "vitest";
import type { ItemRef } from "@/catalog/browse";
import { buildUnitSearch, unitExtent, unitLayers } from "./unit-search";

const { readColumns, readWhere } = vi.hoisted(() => ({ readColumns: vi.fn(), readWhere: vi.fn() }));
vi.mock("@/data/parquet-lite", () => ({ readColumns, readWhere }));

const item = (id: string): ItemRef => ({ collId: "mapping", href: `https://x/${id}.json`,
  data: { id, properties: { title: id }, assets: { data: { href: `https://x/${id}.parquet`, type: "application/vnd.apache.parquet" } } } as never });

const ROWS_500K = [
  { unitsymbol: "Jg", unitname: "Glen Canyon Group (Navajo, Kayenta, Wingate, Moenave Fms) and Nugget Ss", age: "Jurassic" },
  { unitsymbol: "Jg", unitname: "Glen Canyon Group (Navajo, Kayenta, Wingate, Moenave Fms) and Nugget Ss", age: "Jurassic" },
  { unitsymbol: "Ti", unitname: "intrusive rocks", age: "Tertiary" },
  { unitsymbol: "TI", unitname: "lacustrine deposits", age: "Tertiary" },
  { unitsymbol: "Tia", unitname: "andesite", age: "Tertiary" },
];

describe("unitLayers", () => {
  it("keeps the geologic unit layers that have an archive", () => {
    expect(unitLayers([item("geolmap_geolunits_500k"), item("hazards_rockfall")]))
      .toEqual([{ href: "https://x/geolmap_geolunits_500k.json", url: "https://x/geolmap_geolunits_500k.parquet",
        title: "geolmap_geolunits_500k" }]);
  });
});

describe("buildUnitSearch", () => {
  const build = async () => {
    readColumns.mockImplementation(async (url: string) => (url.includes("500k") ? ROWS_500K : [{ mapunit: "Qms" }]));
    return buildUnitSearch(unitLayers([item("geolmap_geolunits_500k"), item("geolmap_geolunits_gems")]));
  };

  it("finds a unit by a word of its name, once per layer", async () => {
    const hits = (await build())("Navajo Sandstone");
    expect(hits.map((h) => h.symbol)).toEqual(["Jg"]);
    expect(hits[0]).toMatchObject({ age: "Jurassic", column: "unitsymbol", layer: "geolmap_geolunits_500k" });
  });

  it("puts the exact symbol first, case first", async () => {
    const search = await build();
    expect(search("Ti").map((h) => h.symbol).slice(0, 2)).toEqual(["Ti", "TI"]);
    expect(search("Qms")[0]).toMatchObject({ symbol: "Qms", column: "mapunit", name: undefined });
  });

  it("zooms to the extent of the unit's polygons", async () => {
    readWhere.mockResolvedValueOnce([
      { unitsymbol: "Ti", bbox_xmin: -112, bbox_ymin: 38, bbox_xmax: -111.5, bbox_ymax: 38.5 },
      { unitsymbol: "Ti", bbox_xmin: -113, bbox_ymin: 39, bbox_xmax: -112.5, bbox_ymax: 39.5 },
    ]);
    const [ti] = (await build())("Ti");
    expect(await unitExtent(ti)).toEqual([-113, 38, -111.5, 39.5]);
    const where = readWhere.mock.calls[0][2];
    expect([where({ unitsymbol: "Ti" }), where({ unitsymbol: "TI" })]).toEqual([true, false]);
  });
});
