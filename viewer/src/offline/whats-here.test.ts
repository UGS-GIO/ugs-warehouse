import { describe, expect, it } from "vitest";
import type { ActiveLayer } from "@/map/map-model";
import { quadsFor, saveBbox, whatsHere } from "./whats-here";

const STATE = [-114.05, 37, -109.04, 42];
const layers: ActiveLayer[] = [
  { id: "faults", title: "Quaternary Faults", pmHref: "https://cdn/faults.pmtiles", bbox: STATE },
  { id: "plate", title: "Geologic map of SLC North", cogHref: "https://cdn/slc.cog.tif", bbox: [-112, 40.75, -111.875, 40.875] },
  { id: "mosaic", title: "1:500,000 mosaic", rasterPmHref: "https://cdn/500k.pmtiles", bbox: STATE },
  { id: "cube", title: "Temperature cube", zarr: { href: "https://cdn/cube.zarr", variable: "t", pinDims: [] }, bbox: STATE },
  { id: "moab", title: "Moab plate", cogHref: "https://cdn/moab.cog.tif", bbox: [-109.6, 38.5, -109.5, 38.6] },
  { id: "nobox", title: "Aspatial table", pmHref: "https://cdn/x.pmtiles" },
];

describe("whatsHere", () => {
  const slc = { kind: "point" as const, lon: -111.9, lat: 40.78 };

  it("lists what covers a point, data layers first", () => {
    expect(whatsHere(layers, slc).map((h) => h.id)).toEqual(["faults", "mosaic", "plate", "cube"]);   // layers, then maps by title
  });

  it("saves vector layers and mosaics by area, plates whole, and cubes not at all", () => {
    const by = Object.fromEntries(whatsHere(layers, slc).map((h) => [h.id, h.save]));
    expect(by.faults).toEqual({ how: "area", url: "https://cdn/faults.pmtiles" });
    expect(by.mosaic).toEqual({ how: "area", url: "https://cdn/500k.pmtiles" });
    expect(by.plate).toEqual({ how: "file", url: "https://cdn/slc.cog.tif" });
    expect(by.cube).toBeNull();
  });

  it("leaves out what is elsewhere, and layers with no footprint", () => {
    const ids = whatsHere(layers, slc).map((h) => h.id);
    expect(ids).not.toContain("moab");
    expect(ids).not.toContain("nobox");
  });

  it("matches an area by overlap", () => {
    const view = { kind: "area" as const, bbox: [-109.7, 38.45, -109.4, 38.65] as [number, number, number, number] };
    expect(whatsHere(layers, view).map((h) => h.id)).toContain("moab");
  });
});

describe("saveBbox / quadsFor", () => {
  // A long press saves the 7.5' quad under the finger, the unit field staff already work in.
  it("turns a point into the quad under it", () => {
    expect(quadsFor({ kind: "point", lon: -111.9, lat: 40.78 }).map((q) => q.code)).toEqual(["40111g8"]);
    expect(saveBbox({ kind: "point", lon: -111.9, lat: 40.78 })).toEqual([-112, 40.75, -111.875, 40.875]);
  });

  it("keeps an area's own bounds", () => {
    const bbox = [-112, 40.7, -111.8, 40.8] as [number, number, number, number];
    expect(saveBbox({ kind: "area", bbox })).toEqual(bbox);
  });
});
