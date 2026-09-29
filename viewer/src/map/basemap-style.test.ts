// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { DIRECT, protomapsStyle } from "./basemap-style";

const sourcesOf = (s: ReturnType<typeof protomapsStyle>) =>
  new Set(s.layers.flatMap((l) => ("source" in l && l.source ? [l.source] : [])));

describe("protomapsStyle", () => {
  it("routes tiles through basemap:// by default, so saved archives are read first", () => {
    expect(protomapsStyle("light").sources.protomaps).toMatchObject({ tiles: ["basemap://{z}/{x}/{y}"], maxzoom: 14 });
  });

  it("can read the statewide file directly", () => {
    expect(protomapsStyle("white", DIRECT).sources.protomaps)
      .toMatchObject({ url: "pmtiles://https://maps-assets.geology.utah.gov/basemap/utah.pmtiles" });
  });

  it("draws every layer from one source, with our glyphs and a bundled sprite", () => {
    const s = protomapsStyle("white");
    expect([...sourcesOf(s)]).toEqual(["protomaps"]);
    expect(s.glyphs).toMatch(/^https:\/\/maps-assets\.geology\.utah\.gov\/styles\/fonts\//);
    expect(s.sprite).toBe(`${location.origin}/basemap-white`);
  });

  it("only asks for fontstacks ugs-styles publishes", () => {
    const hosted = new Set(["Noto Sans Regular", "Noto Sans Medium", "Noto Sans Italic", "Noto Sans Bold",
      "Noto Sans Devanagari Regular v1"]);   // not hosted: only for Devanagari-script names, none in Utah
    const fonts = JSON.stringify(protomapsStyle("light").layers).match(/Noto Sans [A-Za-z]+(?: Regular v1)?/g) ?? [];
    expect(fonts.length).toBeGreaterThan(0);
    expect(fonts.filter((f) => !hosted.has(f))).toEqual([]);
  });
});
