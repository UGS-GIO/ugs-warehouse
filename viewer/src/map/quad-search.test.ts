import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { quadAt } from "@/offline/basemap";
import { isStrongQuad, quadSearch } from "./quad-search";

const ROWS: [string, string][] = JSON.parse(
  readFileSync(new URL("../../public/utah-quads.json", import.meta.url), "utf8"));

describe("the quad gazetteer", () => {
  it("names every Utah quad once, by the basemap's own grid codes", () => {
    expect(ROWS.length).toBeGreaterThan(1500);
    expect(new Set(ROWS.map((r) => r[1])).size).toBe(ROWS.length);
    expect(ROWS).toContainEqual(["Dromedary Peak", quadAt(-111.69, 40.56).code]);
  });
});

describe("quadSearch", () => {
  it("finds a quad by a prefix of its name, with the bbox its code names", async () => {
    const [hit] = (await quadSearch(ROWS))("Dromedary");
    expect(hit).toEqual({ name: "Dromedary Peak", code: "40111e6", bbox: [-111.75, 40.5, -111.625, 40.625] });
    expect(isStrongQuad(hit, "Dromedary")).toBe(true);
  });

  it("puts the exact name ahead of its NE and NW neighbours, with or without 'quad'", async () => {
    const search = await quadSearch(ROWS);
    expect(search("lucin")[0].name).toBe("Lucin");
    expect(search("Lucin quad").map((h) => h.name)).toContain("Lucin NE");
  });

  it("ranks a name starting with the text over one only containing it", async () => {
    const strong = (await quadSearch(ROWS))("mon").map((h) => isStrongQuad(h, "mon"));
    expect(strong).toContain(true);
    expect(strong).toEqual([...strong].sort().reverse());   // every strong hit before any weak one
    expect((await quadSearch(ROWS))("x")).toEqual([]);
  });
});
