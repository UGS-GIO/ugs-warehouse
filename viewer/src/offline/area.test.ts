import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { FileSource, PMTiles } from "pmtiles";
import { describe, expect, it } from "vitest";
import { planArea, tilesAt, type Bbox } from "./area";

// A real published-shape archive: one basemap quad (37112d1), z11-14, 70 tiles, gzip MVT.
const bytes = readFileSync(fileURLToPath(new URL("./__fixtures__/quad-37112d1.pmtiles", import.meta.url)));
const archive = () => new PMTiles(new FileSource(new File([bytes], "quad.pmtiles")));
const QUAD: Bbox = [-112.125, 37.375, -112.0, 37.5];
const URL_ = "https://cdn.example/quad.pmtiles";

describe("tilesAt", () => {
  it("covers a bbox with the tiles it touches", () => {
    expect(tilesAt(QUAD, 0)).toEqual([[0, 0]]);
    const z14 = tilesAt(QUAD, 14);
    expect(z14.length).toBeGreaterThan(20);
    expect(new Set(z14.map(([x, y]) => `${x}/${y}`)).size).toBe(z14.length);   // no duplicates
  });
});

describe("planArea", () => {
  it("finds every tile of the archive inside its own bounds, from the directory alone", async () => {
    const plan = await planArea(URL_, QUAD, archive());
    expect(plan.tiles).toHaveLength(70);
    expect(plan.meta.tilejson).toMatchObject({ minzoom: 11, maxzoom: 14 });
    expect(plan.meta.tilejson.tiles[0]).toBe(`pmtiles://${URL_}/{z}/{x}/{y}`);
  });

  // The price shown before saving must be what saving actually costs.
  it("quotes exactly the bytes a save will fetch", async () => {
    const p = archive();
    const plan = await planArea(URL_, QUAD, p);
    let fetched = 0;
    for (const t of plan.tiles) fetched += (await p.source.getBytes(t.offset, t.length)).data.byteLength;
    expect(plan.bytes).toBe(fetched);
    expect(plan.bytes).toBeGreaterThan(0);
  });

  it("plans a smaller area as a strict subset", async () => {
    const whole = await planArea(URL_, QUAD, archive());
    const corner = await planArea(URL_, [-112.125, 37.375, -112.09, 37.41], archive());
    const key = (t: { z: number; x: number; y: number }) => `${t.z}/${t.x}/${t.y}`;
    const all = new Set(whole.tiles.map(key));
    expect(corner.tiles.length).toBeLessThan(whole.tiles.length);
    expect(corner.tiles.every((t) => all.has(key(t)))).toBe(true);
    expect(corner.bytes).toBeLessThan(whole.bytes);
  });

  it("returns nothing for an area the archive does not cover", async () => {
    const plan = await planArea(URL_, [-109.2, 41.8, -109.1, 41.9], archive());
    expect(plan.tiles.filter((t) => t.z >= 12)).toHaveLength(0);
  });
});
