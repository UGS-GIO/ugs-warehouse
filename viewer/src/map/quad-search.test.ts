import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { quadAt } from "@/offline/basemap";
import { type Gazetteer, isStrongQuad, quadSearch } from "./quad-search";

const G: Gazetteer = JSON.parse(
  readFileSync(new URL("../../public/utah-quads.json", import.meta.url), "utf8"));

describe("the quad gazetteer", () => {
  it("names every Utah 7.5' quad once, by the basemap's own grid codes", () => {
    expect(G["24k"].length).toBeGreaterThan(1500);
    expect(new Set(G["24k"].map((r) => r[1])).size).toBe(G["24k"].length);
    expect(G["24k"]).toContainEqual(["Dromedary Peak", quadAt(-111.69, 40.56).code]);
  });

  it("names Utah's 30'x60' sheets by their SE 7.5' cell", () => {
    expect(G["100k"].length).toBeGreaterThan(40);
    expect(G["100k"]).toContainEqual(["Salt Lake City", quadAt(-111.01, 40.51).code]);
  });
});

describe("quadSearch", () => {
  it("finds a quad by a prefix of its name, with the bbox its code names", async () => {
    const [hit] = (await quadSearch(G))("Dromedary");
    expect(hit).toEqual({ name: "Dromedary Peak", code: "40111e6", scale: "24k", bbox: [-111.75, 40.5, -111.625, 40.625] });
    expect(isStrongQuad(hit, "Dromedary")).toBe(true);
  });

  it("puts the exact name ahead of its NE and NW neighbours, with or without 'quad'", async () => {
    const search = await quadSearch(G);
    expect(search("lucin")[0].name).toBe("Lucin");
    expect(search("Lucin quad").map((h) => h.name)).toContain("Lucin NE");
  });

  it("finds a 30'x60' sheet, and only sheets when the text says 30x60", async () => {
    const search = await quadSearch(G);
    expect(search("Salt Lake City")).toContainEqual(
      { name: "Salt Lake City", code: "40111e1", scale: "100k", bbox: [-112, 40.5, -111, 41] });
    for (const q of ["Salt Lake City 30x60", "salt lake city 30'x60' quadrangle", "Moab 100k"]) {
      const hits = search(q);
      expect(hits.length, q).toBeGreaterThan(0);
      expect(hits.every((h) => h.scale === "100k"), q).toBe(true);
    }
  });

  it("ranks a name starting with the text over one only containing it", async () => {
    const strong = (await quadSearch(G))("mon").map((h) => isStrongQuad(h, "mon"));
    expect(strong).toContain(true);
    expect(strong).toEqual([...strong].sort().reverse());   // every strong hit before any weak one
    expect((await quadSearch(G))("x")).toEqual([]);
  });
});
