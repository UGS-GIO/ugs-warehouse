import { describe, expect, it } from "vitest";
import type { ActiveLayer } from "@/map/map-model";
import type { Bbox } from "./guards";
import {
  bestMap, caveatOf, mapOrder, otherUnit, probeOf, quadsFor, ringAround, saveBbox, type Target, toleranceM, unitOf,
  whatsHere,
} from "./whats-here";

const STATE = [-114.05, 37, -109.04, 42];
const layers: ActiveLayer[] = [
  { id: "faults", title: "Quaternary Faults", pmHref: "https://cdn/faults.pmtiles", bbox: STATE },
  { id: "plate", title: "Geologic map of SLC North", cogHref: "https://cdn/slc.cog.tif", bbox: [-112, 40.75, -111.875, 40.875] },
  { id: "mosaic", title: "1:500,000 mosaic", rasterPmHref: "https://cdn/500k.pmtiles", bbox: STATE },
  { id: "cube", title: "Temperature cube", zarr: { href: "https://cdn/cube.zarr", variable: "t", variables: ["t"], stepDims: {}, selection: {} }, bbox: STATE },
  { id: "moab", title: "Moab plate", cogHref: "https://cdn/moab.cog.tif", bbox: [-109.6, 38.5, -109.5, 38.6] },
  { id: "nobox", title: "Aspatial table", pmHref: "https://cdn/x.pmtiles" },
];

const slc = { kind: "point" as const, lon: -111.9, lat: 40.78 };

describe("whatsHere", () => {
  it("lists what covers a point, data layers first", () => {
    expect(whatsHere(layers, slc).map((h) => h.id)).toEqual(["faults", "mosaic", "plate", "cube"]);   // layers, then maps by title
  });

  it("saves vector layers, mosaics and plates by area, and cubes not at all", () => {
    const by = Object.fromEntries(whatsHere(layers, slc).map((h) => [h.id, h.save]));
    expect(by.faults).toEqual({ how: "area", url: "https://cdn/faults.pmtiles" });
    expect(by.mosaic).toEqual({ how: "area", url: "https://cdn/500k.pmtiles" });
    expect(by.plate).toEqual({ how: "cog", url: "https://cdn/slc.cog.tif" });
    expect(by.cube).toBeNull();
  });

  it("offers a layer's table as its own row, after the data layers", () => {
    const withTable = [{ ...layers[0], tableHref: "https://cdn/faults.parquet" }, ...layers.slice(1)];
    const here = whatsHere(withTable, slc);
    expect(here.map((h) => h.id)).toEqual(["faults", "faults#table", "mosaic", "plate", "cube"]);
    expect(here[1]).toMatchObject({ group: "table", save: { how: "table", url: "https://cdn/faults.parquet" } });
  });

  it("leaves out what is elsewhere, and layers with no footprint", () => {
    const ids = whatsHere(layers, slc).map((h) => h.id);
    expect(ids).not.toContain("moab");
    expect(ids).not.toContain("nobox");
  });

  it("matches an area by overlap", () => {
    const view: Target = { kind: "area", bbox: [-109.7, 38.45, -109.4, 38.65] };
    expect(whatsHere(layers, view).map((h) => h.id)).toContain("moab");
  });
});

describe("probeOf", () => {
  // A long press lists only what is really there, so each row is checked the cheapest true way.
  it("asks vectors for features, mosaics for a tile, plates by bbox, and leaves out cubes", () => {
    const withTable = [{ ...layers[0], tableHref: "https://cdn/faults.parquet" }, ...layers.slice(1)];
    const by = Object.fromEntries(whatsHere(withTable, slc).map((h) => [h.id, probeOf(h)]));
    expect(by).toEqual({ faults: "identify", "faults#table": null, mosaic: "tile", plate: "bbox", cube: null });
  });
});

describe("saveBbox / quadsFor", () => {
  // A long press saves the 7.5' quad under the finger, the unit field staff already work in.
  it("turns a point into the quad under it", () => {
    expect(quadsFor({ kind: "point", lon: -111.9, lat: 40.78 }).map((q) => q.code)).toEqual(["40111g8"]);
    expect(saveBbox({ kind: "point", lon: -111.9, lat: 40.78 })).toEqual([-112, 40.75, -111.875, 40.875]);
  });

  it("keeps an area's own bounds", () => {
    const bbox: Bbox = [-112, 40.7, -111.8, 40.8];
    expect(saveBbox({ kind: "area", bbox })).toEqual(bbox);
  });
});

describe("geology", () => {
  it("names a unit from each unit layer's own fields, and nothing from other layers", () => {
    expect(unitOf("geolmap_geolunits_500k", { unitname: "intrusive rocks-Tertiary", unitsymbol: "Ti", age: "Tertiary" }))
      .toEqual({ name: "intrusive rocks-Tertiary", symbol: "Ti", age: "Tertiary", scale: 500000 });
    expect(unitOf("geolmap_geolunits_gems", { mapunit: "Qal", scale: "24000" })).toMatchObject({ symbol: "Qal", scale: 24000 });
    expect(unitOf("enmin_ut_counties", { name: "SALT LAKE" })).toBeNull();
    expect(unitOf("constructor", {})).toBeNull();
  });

  it("prefers the most detailed scale tier, unknown scales last", () => {
    expect(bestMap([{ id: "a", scale: 500000 }, { id: "b" }, { id: "c", scale: 24000 }])?.id).toBe("c");
    expect(bestMap([])).toBeUndefined();
  });

  it("prefers the newer map within a scale tier, not an older finer one", () => {
    const old = { id: "1975", scale: 12000, year: 1975 };
    const recent = { id: "2015", scale: 24000, year: 2015 };
    expect(bestMap([old, recent])?.id).toBe("2015");
    expect(bestMap([{ id: "62k", scale: 62500, year: 2020 }, old])?.id).toBe("1975");
    expect(mapOrder(recent, { scale: 24000, year: 1987 })).toBeLessThan(0);
    expect(mapOrder({ scale: 24000, year: 1987 }, { scale: 24000, year: 1987 })).toBe(0);
  });

  it("warns by scale: nothing at 1:24,000, ±50 m at 1:100,000, generalized at 1:500,000", () => {
    expect(caveatOf(24000)).toBeNull();
    expect(caveatOf(undefined)).toBeNull();
    expect(caveatOf(100000)).toBe("Boundaries approximate (about ±50 m).");
    expect(caveatOf(500000)).toMatch(/^Generalized statewide map; boundaries can be off by several hundred meters/);
  });

  it("looks a map's tolerance away for another unit", () => {
    expect(toleranceM(500000)).toBe(250);
    const ring = ringAround(-111.7, 40.5594, 250);
    expect(ring).toHaveLength(8);
    expect(ring[2][1] - 40.5594).toBeCloseTo(250 / 111_320, 6);   // due north, 250 m
    const ti = { symbol: "Ti" };
    expect(otherUnit(ti, [ti, null, { symbol: "Qal", name: "alluvium" }])?.name).toBe("alluvium");
    expect(otherUnit(ti, [ti, undefined])).toBeUndefined();
  });

  it("carries a plate's units archive through", () => {
    const plate = { ...layers[1], unitsHref: "https://cdn/slc-units.pmtiles" };
    expect(whatsHere([plate], slc)[0].unitsHref).toBe("https://cdn/slc-units.pmtiles");
  });
});
