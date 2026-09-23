import { describe, expect, it } from "vitest";
import { archiveFor, OVERVIEW_MAXZOOM, overviewUrl, pickArchive, quadAt, quadsInBbox, quadUrl, redundantWithState,
  rerouteStyle, tileCenter } from "./basemap";

/** The z/x/y tile containing a point (standard web-mercator XYZ). */
const tileAt = (lon: number, lat: number, z: number): [number, number, number] => {
  const n = 2 ** z;
  const r = (lat * Math.PI) / 180;
  return [z, Math.floor(((lon + 180) / 360) * n),
    Math.floor(((1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2) * n)];
};
const SLC = tileAt(-111.89, 40.76, 14);   // downtown Salt Lake City, inside 40111g8

// Same fixtures as tests/test_basemap_quads.py. The build names the archives in Python and the
// viewer asks for them in TypeScript; if these ever disagree, a downloaded quad is never read.
describe("quadAt", () => {
  it("names real quads by their USGS Ohio code", () => {
    expect(quadAt(-111.89, 40.76).code).toBe("40111g8");   // Salt Lake City North
    expect(quadAt(-111.66, 40.23).code).toBe("40111b6");   // Provo
  });

  it("puts a point on a block's SE corner in that block's a1 cell", () => {
    expect(quadAt(-111.0, 40.0).code).toBe("40111a1");
  });

  it("returns a 7.5-minute cell containing the point", () => {
    const q = quadAt(-111.89, 40.76);
    expect(q.east - q.west).toBeCloseTo(0.125);
    expect(q.west).toBeLessThanOrEqual(-111.89);
    expect(q.east).toBeGreaterThanOrEqual(-111.89);
  });
});

describe("tileCenter", () => {
  it("puts z0's single tile at the origin", () => {
    const [lon, lat] = tileCenter(0, 0, 0);
    expect(lon).toBeCloseTo(0);
    expect(lat).toBeCloseTo(0);
  });

  it("lands a z14 Salt Lake City tile in Salt Lake City North", () => {
    const [lon, lat] = tileCenter(...SLC);
    expect(quadAt(lon, lat).code).toBe("40111g8");
  });
});

describe("archiveFor", () => {
  it("reads low zooms from the statewide overview", () => {
    expect(archiveFor(OVERVIEW_MAXZOOM, 190, 390)).toBe(overviewUrl());
    expect(archiveFor(3, 1, 3)).toBe(overviewUrl());
  });

  it("reads high zooms from the quad under the tile", () => {
    expect(archiveFor(...SLC)).toBe(quadUrl("40111g8"));
  });
});

describe("quadsInBbox", () => {
  it("covers a viewport with every quad it touches, once each", () => {
    // A box spanning the corner of four quads.
    const codes = quadsInBbox([-111.93, 40.72, -111.82, 40.80]).map((q) => q.code).sort();
    expect(codes).toEqual(["40111f8", "40111g8"].concat(["40111f7", "40111g7"]).sort());
  });

  it("returns one quad for a view inside a single quad", () => {
    expect(quadsInBbox([-111.9, 40.76, -111.88, 40.77]).map((q) => q.code)).toEqual(["40111g8"]);
  });
});

describe("rerouteStyle", () => {
  const ofm = {
    version: 8, sprite: "https://tiles.openfreemap.org/sprites/ofm", layers: [{ id: "water" }],
    sources: {
      ne2_shaded: { type: "raster", tiles: ["https://tiles.openfreemap.org/natural_earth/{z}/{x}/{y}.png"] },
      openmaptiles: { type: "vector", url: "https://tiles.openfreemap.org/planet" },
    },
  };

  it("routes only the vector source through basemap://", () => {
    const s = rerouteStyle(ofm);
    expect(s.sources.openmaptiles).toMatchObject({ type: "vector", tiles: ["basemap://{z}/{x}/{y}"], maxzoom: 14 });
    expect(s.sources.openmaptiles.url).toBeUndefined();
  });

  it("leaves layers, sprite and the other sources exactly as shipped", () => {
    const s = rerouteStyle(ofm);
    expect(s.layers).toBe(ofm.layers);
    expect(s.sprite).toBe(ofm.sprite);
    expect(s.sources.ne2_shaded).toBe(ofm.sources.ne2_shaded);
  });

  it("keeps attribution on the rerouted source", () => {
    const src = rerouteStyle(ofm).sources.openmaptiles as { attribution?: string };
    expect(src.attribution).toMatch(/OpenStreetMap/);
  });

  it("does not touch a style without an OpenMapTiles vector source", () => {
    const sat = { version: 8, sources: { sat: { type: "raster" } }, layers: [] };
    expect(rerouteStyle(sat)).toBe(sat);
  });
});

describe("pickArchive", () => {
  const B = "https://cdn.example/basemap/";

  it("reads everything from the statewide file once it is saved", () => {
    const saved = new Set([`${B}utah.pmtiles`, `${B}overview.pmtiles`]);
    expect(pickArchive(3, 1, 3, saved, B)).toBe(`${B}utah.pmtiles`);
    expect(pickArchive(...SLC, saved, B)).toBe(`${B}utah.pmtiles`);
  });

  it("falls back to the overview and quads when only those are saved", () => {
    const saved = new Set([`${B}overview.pmtiles`]);
    expect(pickArchive(3, 1, 3, saved, B)).toBe(`${B}overview.pmtiles`);
    expect(pickArchive(...SLC, saved, B)).toBe(`${B}quads/40111g8.pmtiles`);
  });
});

describe("redundantWithState", () => {
  const B = "https://cdn.example/basemap/";

  it("lists the overview and every quad, and nothing else", () => {
    const saved = [`${B}utah.pmtiles`, `${B}overview.pmtiles`, `${B}quads/40111g8.pmtiles`,
      `${B}quads/40111b6.pmtiles`, "https://cdn.example/warehouse/pmtiles/layer.pmtiles"];
    expect(redundantWithState(saved, B).sort()).toEqual(
      [`${B}overview.pmtiles`, `${B}quads/40111b6.pmtiles`, `${B}quads/40111g8.pmtiles`]);
  });
});
