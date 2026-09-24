import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { VectorTile } from "@mapbox/vector-tile";
import Pbf from "pbf";
import { FileSource, PMTiles } from "pmtiles";
import { describe, expect, it } from "vitest";
import { hitLabel, hits, identifyAt, inRings, toSegment } from "./identify";

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
    expect(hitLabel({ id: "groundshaking_current.fid-23e49f4e_1a07e84e73d_-1143", pga: "0.4g" })).toBe("0.4g");
    expect(hitLabel({ globalid: "0b1f8c2e-1234-4d5e-9abc-001122334455" })).toBeNull();
  });

  it("falls back to the first short text", () => {
    expect(hitLabel({ objectid: 7, hazard: "High" })).toBe("High");
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
    const toTile = (lon: number, lat: number) => {
      const r = (lat * Math.PI) / 180;
      return [Math.floor(((lon + 180) / 360) * n), Math.floor(((1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2) * n)];
    };
    const [tx, ty] = toTile((h.minLon + h.maxLon) / 2, (h.minLat + h.maxLat) / 2);
    const tile = await p.getZxy(z, tx, ty);
    const vt = new VectorTile(new Pbf(new Uint8Array(tile!.data)));
    let found: { layer: string; lon: number; lat: number } | null = null;
    for (const name of Object.keys(vt.layers)) {
      const layer = vt.layers[name];
      for (let i = 0; i < layer.length && !found; i++) {
        const f = layer.feature(i);
        if (f.type !== 2) continue;
        const v = f.loadGeometry()[0][0];
        const lon = ((tx + v.x / layer.extent) / n) * 360 - 180;
        const lat = (Math.atan(Math.sinh(Math.PI * (1 - (2 * (ty + v.y / layer.extent)) / n))) * 180) / Math.PI;
        if (lon > h.minLon && lon < h.maxLon && lat > h.minLat && lat < h.maxLat) found = { layer: name, lon, lat };
      }
    }
    expect(found).not.toBeNull();
    const got = await identifyAt("fixture", found!.lon, found!.lat, 15, archive());
    expect(got.some((g) => g.layer === found!.layer)).toBe(true);

    expect(await identifyAt("fixture", -100, 30, 15, archive())).toEqual([]);   // Texas: not in this quad
  });
});
