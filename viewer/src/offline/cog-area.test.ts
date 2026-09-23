import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { fromArrayBuffer } from "geotiff";
import { describe, expect, it } from "vitest";
import type { Bbox } from "./area";
import { assemble, planCogArea } from "./cog-area";

// A real COG shaped like our plates: EPSG:3857 over the Salt Lake City North quad, 1024 px,
// 256 px tiles, DEFLATE, two overview levels. Built with GDAL's COG driver.
const file = readFileSync(fileURLToPath(new URL("./__fixtures__/plate-40111g8.cog.tif", import.meta.url)));
const bytes = new Uint8Array(file.buffer, file.byteOffset, file.byteLength);
const buffer = () => bytes.slice().buffer;
const QUAD: Bbox = [-112, 40.75, -111.875, 40.875];
// Just inside the north-west quarter. Tiles that merely TOUCH the area are kept (as pmtiles
// extract does), so a box sitting exactly on tile edges would also take the neighbours; and in
// web mercator the quad's pixel midpoint is at lat ~40.8126, not the 40.8125 midpoint in degrees.
const CORNER: Bbox = [-111.999, 40.815, -111.94, 40.874];
const BLOCK = 1024;                                        // small, so the cut is visible in a 35 KB file

const plan = async (bbox: Bbox) =>
  planCogArea("https://cdn/plate.cog.tif", bbox, { tiff: await fromArrayBuffer(buffer()), size: bytes.length, block: BLOCK });

/** The file as a reader would see it offline: saved blocks present, everything else zeroed. */
function sparse(blocks: number[]): ArrayBuffer {
  const out = new Uint8Array(bytes.length);
  for (const b of blocks) out.set(bytes.subarray(b * BLOCK, (b + 1) * BLOCK), b * BLOCK);
  return out.buffer;
}

const pixels = async (buf: ArrayBuffer, image: number, window: [number, number, number, number]) =>
  (await (await fromArrayBuffer(buf)).getImage(image)).readRasters({ window, interleave: true });

describe("planCogArea", () => {
  it("keeps the whole file when the area covers the whole image", async () => {
    const p = await plan(QUAD);
    const all = Math.ceil(bytes.length / BLOCK);
    expect(p.blocks.length).toBeGreaterThan(all * 0.9);
  });

  // Tile counts, not a byte ratio: in a tiny flat-colour fixture the header is a large share of
  // the file, where in a real 700 MB plate the tiles dominate.
  it("takes only the tiles inside the area, at every overview level", async () => {
    const whole = await plan(QUAD);
    const corner = await plan(CORNER);
    expect(whole.tiles).toBe(16 + 4 + 1);    // 4x4 full res, 2x2 and 1x1 overviews
    expect(corner.tiles).toBe(4 + 1 + 1);    // the north-west quarter of each
    expect(corner.bytes).toBeLessThan(whole.bytes);
    expect(corner.blocks.every((b) => whole.blocks.includes(b))).toBe(true);
  });

  // The test that matters: from only the saved blocks, a reader draws the area exactly as from
  // the full file, at full resolution and at an overview level.
  it("saves everything a reader needs to draw the area", async () => {
    const { blocks } = await plan(CORNER);
    const cut = sparse(blocks);
    for (const [image, window] of [[0, [0, 0, 512, 512]], [1, [0, 0, 256, 256]]] as const) {
      const want = await pixels(buffer(), image, [...window]);
      const got = await pixels(cut, image, [...window]);
      expect(Array.from(got as Uint8Array)).toEqual(Array.from(want as Uint8Array));
    }
  });

  it("prices exactly the bytes of the blocks it will fetch", async () => {
    const p = await plan(CORNER);
    const expected = p.blocks.reduce((n, b) => n + Math.min(BLOCK, bytes.length - b * BLOCK), 0);
    expect(p.bytes).toBe(expected);
  });
});

describe("assemble", () => {
  const get = (i: number) => bytes.slice(i * BLOCK, (i + 1) * BLOCK);

  it("rebuilds any range from blocks, including ones that straddle block edges", () => {
    for (const [s, e] of [[0, 99], [1000, 1100], [BLOCK * 3 + 7, BLOCK * 5 + 11]]) {
      expect(Array.from(assemble(s, e, BLOCK, get)!)).toEqual(Array.from(bytes.subarray(s, e + 1)));
    }
  });

  it("returns the file's last partial block correctly", () => {
    const last = bytes.length - 1;
    expect(Array.from(assemble(last - 50, last, BLOCK, get)!)).toEqual(Array.from(bytes.subarray(last - 50)));
  });

  it("reports a missing block rather than inventing bytes", () => {
    expect(assemble(0, BLOCK * 2, BLOCK, (i) => (i === 1 ? null : get(i)))).toBeNull();
  });
});
