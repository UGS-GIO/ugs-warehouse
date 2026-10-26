import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { VectorTile } from "@mapbox/vector-tile";
import Pbf from "pbf";
import { FileSource, PMTiles } from "pmtiles";
import { MercatorCoordinate } from "maplibre-gl";
import { describe, expect, it } from "vitest";
import { hitLabel, hits, identifyAt, identifyPoints, inRings, tileAt, toSegment } from "./identify";

describe("geometry", () => {
  const square = [[{ x: 0, y: 0 }, { x: 10, y: 0 }, { x: 10, y: 10 }, { x: 0, y: 10 }, { x: 0, y: 0 }]];
  const hole = [{ x: 3, y: 3 }, { x: 7, y: 3 }, { x: 7, y: 7 }, { x: 3, y: 7 }, { x: 3, y: 3 }];

  it("finds a point inside a polygon, and not in its hole", () => {
    expect(inRings({ x: 1, y: 1 }, square)).toBe(true);
    expect(inRings({ x: 20, y: 1 }, square)).toBe(false);
    expect(inRings({ x: 5, y: 5 }, [...square, hole])).toBe(false);
    expect(inRings({ x: 1, y: 5 }, [...square, hole])).toBe(true);
  });

  it("measures distance to a segment, clamped to its ends", () => {
    expect(toSegment({ x: 5, y: 3 }, { x: 0, y: 0 }, { x: 10, y: 0 })).toBe(3);
    expect(toSegment({ x: 13, y: 4 }, { x: 0, y: 0 }, { x: 10, y: 0 })).toBe(5);
  });

  it("hits lines and points only within the tolerance", () => {
    const line = [[{ x: 0, y: 0 }, { x: 100, y: 0 }]];
    expect(hits(2, line, { x: 50, y: 4 }, 5)).toBe(true);
    expect(hits(2, line, { x: 50, y: 6 }, 5)).toBe(false);
    expect(hits(1, [[{ x: 10, y: 10 }]], { x: 12, y: 11 }, 5)).toBe(true);
  });
});

describe("hitLabel", () => {
  it("prefers a name-like attribute", () => {
    expect(hitLabel({ objectid: 7, unitname: "Lake Bonneville deposits", age: "Q" })).toBe("Lake Bonneville deposits");
  });
  it("skips identifiers, which read as noise", () => {
    expect(hitLabel({ id: "groundshaking_current.fid-23e49f4e_1a07e84e73d_-1143", pga: "Very strong" })).toBe("Very strong");
    expect(hitLabel({ globalid: "0b1f8c2e-1234-4d5e-9abc-001122334455" })).toBeNull();
  });

  it("prefers a name over a class or code that comes first", () => {
    expect(hitLabel({ class: "First", countynbr: "18", name: "SALT LAKE" })).toBe("SALT LAKE");
    expect(hitLabel({ cbsafp2: "41620", classfp: "H1", name20: "Salt Lake" })).toBe("Salt Lake");
  });

  it("falls back to text that reads as words, never a code, number or bookkeeping", () => {
    expect(hitLabel({ objectid: 7, hazard: "High" })).toBe("High");
    expect(hitLabel({ egshazardunit: "VSSegs", acc: "0.4" })).toBeNull();
    expect(hitLabel({ table_type: "dimension", _publication_date: "2026-09-22" })).toBeNull();
    expect(hitLabel({ objectid: 7 })).toBeNull();
  });
});

// Against a real archive: take a line feature's own vertex and ask what is there. Whatever the
// basemap quad holds, a point on a line must find that line.
describe("identifyAt (real PMTiles)", () => {
  const bytes = readFileSync(fileURLToPath(new URL("./__fixtures__/quad-37112d1.pmtiles", import.meta.url)));
  const archive = () => new PMTiles(new FileSource(new File([bytes], "quad.pmtiles")));

  it("finds a line at one of its own vertices, and nothing far outside the archive", async () => {
    const p = archive();
    const h = await p.getHeader();
    const z = Math.min(h.maxZoom, 14);
    // any z14 tile in the archive with a line in it
    const n = 2 ** z;
    const toTile = (lng: number, lat: number) => {
      const { x, y } = MercatorCoordinate.fromLngLat({ lng, lat });
      return [Math.floor(x * n), Math.floor(y * n)];
    };
    const [tx, ty] = toTile((h.minLon + h.maxLon) / 2, (h.minLat + h.maxLat) / 2);
    const tile = await p.getZxy(z, tx, ty);
    if (!tile) throw new Error("the fixture has no tile at its centre");
    const vt = new VectorTile(new Pbf(new Uint8Array(tile.data)));
    let found: { layer: string; lon: number; lat: number } | null = null;
    for (const name of Object.keys(vt.layers)) {
      const layer = vt.layers[name];
      for (let i = 0; i < layer.length && !found; i++) {
        const f = layer.feature(i);
        if (f.type !== 2) continue;
        const v = f.loadGeometry()[0][0];
        const { lng: lon, lat } = new MercatorCoordinate((tx + v.x / layer.extent) / n, (ty + v.y / layer.extent) / n).toLngLat();
        if (lon > h.minLon && lon < h.maxLon && lat > h.minLat && lat < h.maxLat) found = { layer: name, lon, lat };
      }
    }
    if (!found) throw new Error("the fixture's centre tile has no line inside the archive");
    const { layer, lon, lat } = found;
    const got = await identifyAt("fixture", lon, lat, 15, archive());
    expect(got.some((g) => g.layer === layer)).toBe(true);

    expect(await identifyAt("fixture", -100, 30, 15, archive())).toEqual([]);   // Texas: not in this quad
  });

  it("answers the other points when one tile fails to load", async () => {
    const p = archive();
    const h = await p.getHeader();
    const real = p.getZxy.bind(p);
    let calls = 0;
    p.getZxy = (...a: Parameters<PMTiles["getZxy"]>) => (calls++ === 0 ? Promise.reject(new Error("network")) : real(...a));
    const mid: [number, number] = [(h.minLon + h.maxLon) / 2, (h.minLat + h.maxLat) / 2];
    const got = await identifyPoints("fixture", [[-100, 30], mid], 15, p);
    expect(got[0]).toEqual([]);
    expect(Array.isArray(got[1])).toBe(true);
    expect(calls).toBe(2);
  });

  it("rejects when aborted", async () => {
    const ac = new AbortController();
    ac.abort(new Error("timed out"));
    await expect(identifyAt("fixture", -100, 30, 15, archive(), ac.signal)).rejects.toThrow("timed out");
  });

  it("has a tile where the archive covers, and none outside it", async () => {
    const h = await archive().getHeader();
    const mid = [(h.minLon + h.maxLon) / 2, (h.minLat + h.maxLat) / 2] as const;
    expect(await tileAt("fixture", mid[0], mid[1], archive())).toBe(true);
    expect(await tileAt("fixture", -100, 30, archive())).toBe(false);
  });
});
