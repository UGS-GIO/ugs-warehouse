// Save part of a COG for offline use: only the bytes a reader needs to draw an area.
//
// Our plates are hundreds of MB whole (the statewide one is 2.9 GB), so a COG is cut to the area
// just like a PMTiles layer. A COG keeps its header, image directories and tile-offset tables at
// the front of the file, before any tile data; so "everything before the first tile" plus "the
// tiles inside the area, at every overview level" is exactly what a reader fetches to draw it.
//
// Stored in fixed blocks, because that is how the reader asks: the geomatico COG protocol opens
// files with geotiff.js at a 64 KB block size, so every range it requests is block-aligned. The
// service worker assembles a requested range from stored blocks (cogs/<encoded url>/<index>), and
// a range that needs a block we did not save goes to the network, or fails cleanly offline.
import { type BlockedSourceOptions, fromUrl, type GeoTIFF, type RemoteSourceOptions, type TypedArray } from "geotiff";
import { MercatorCoordinate } from "maplibre-gl";
import type { Bbox } from "./area";
import { versionOf } from "./opfs-name";

import { dropIfStale } from "./block-store";
import { blocksOf } from "./cog-blocks";

export { assemble } from "./cog-blocks";

/** Must equal the block size the COG reader requests in (geomatico's CogReader, 65536). */
export const COG_BLOCK = 65536;

import type { CogPlan } from "./block-store";

export type { CogPlan };

// EPSG:3857's full width in metres: the plates' bounding boxes are in it, maplibre's projection
// is normalised to 0..1, and this is the scale between the two.
const WORLD = 2 * 20037508.342789244;
const merc = (lng: number, lat: number): [number, number] => {
  const { x, y } = MercatorCoordinate.fromLngLat({ lng, lat: Math.max(-85.0511, Math.min(85.0511, lat)) });
  return [(x - 0.5) * WORLD, (0.5 - y) * WORLD];
};

async function fileHead(url: string): Promise<{ size: number; version?: string }> {
  const r = await fetch(url, { method: "HEAD", cache: "no-store" });   // the live file, not a saved copy
  if (!r.ok) throw new Error(`${r.status}`);
  return { size: Number(r.headers.get("content-length")), version: versionOf(r.headers) };
}

/** A TIFF tag's values as numbers; a tiled COG without them cannot be cut. */
function tagNumbers(v: number | number[] | TypedArray | undefined, tag: string): number[] {
  if (v === undefined) throw new Error(`This COG has no ${tag}; it can only be saved whole.`);
  return typeof v === "number" ? [v] : Array.from(v);
}

/** Blocks covering [start, start + length). */

/**
 * The blocks of a COG needed to draw `bbox` at every overview level, priced exactly. Reads only
 * the directory at the front of the file; no tile is downloaded to plan.
 */
export async function planCogArea(url: string, bbox: Bbox,
  opts: { tiff?: GeoTIFF; size?: number; block?: number } = {}): Promise<CogPlan> {
  const block = opts.block ?? COG_BLOCK;
  // Planning reads the file through the service worker; a saved copy of an older version would
  // hand it the old directory. Drop that copy first, so the plan is cut from the live file.
  const head = opts.size ? { size: opts.size, version: undefined } : await fileHead(url);
  if (!opts.tiff) await dropIfStale(url, head.version);
  // Read in the reader's own block size, so planning warms the same blocks drawing will ask for.
  // fromUrl passes its options on to the blocked source, whose options type carries blockSize.
  const options: RemoteSourceOptions & BlockedSourceOptions = { blockSize: COG_BLOCK };
  const tiff = opts.tiff ?? await fromUrl(url, options);
  const size = head.size;
  const count = await tiff.getImageCount();
  const [minX, minY, maxX, maxY] = (await tiff.getImage(0)).getBoundingBox();
  const [ax0, ay0] = merc(bbox[0], bbox[1]);
  const [ax1, ay1] = merc(bbox[2], bbox[3]);
  const need = new Set<number>();
  let firstTile = Infinity;
  let tiles = 0;

  for (let i = 0; i < count; i++) {
    const img = await tiff.getImage(i);
    if (!img.isTiled) throw new Error("Not a tiled COG; it can only be saved whole.");
    const fd = img.fileDirectory;
    const offsets = tagNumbers(await fd.loadValue("TileOffsets"), "TileOffsets");
    const counts = tagNumbers(await fd.loadValue("TileByteCounts"), "TileByteCounts");
    for (const o of offsets) if (o > 0 && o < firstTile) firstTile = o;

    // Every IFD (overviews, internal masks) covers the full-resolution image's extent at its own
    // pixel size, so the area maps to a tile window by fraction of that extent.
    const tw = img.getTileWidth();
    const th = img.getTileHeight();
    const across = Math.ceil(img.getWidth() / tw);
    const down = Math.ceil(img.getHeight() / th);
    const col = (x: number) => Math.floor(((x - minX) / (maxX - minX)) * img.getWidth() / tw);
    const row = (y: number) => Math.floor(((maxY - y) / (maxY - minY)) * img.getHeight() / th);
    const c0 = Math.max(0, col(ax0)), c1 = Math.min(across - 1, col(ax1));
    const r0 = Math.max(0, row(ay1)), r1 = Math.min(down - 1, row(ay0));
    const planes = fd.getValue("PlanarConfiguration") === 2 ? fd.getValue("SamplesPerPixel") ?? 1 : 1;
    for (let p = 0; p < planes; p++) {
      for (let r = r0; r <= r1; r++) {
        for (let c = c0; c <= c1; c++) {
          const idx = p * across * down + r * across + c;
          if (counts[idx] > 0) {
            tiles++;
            for (const b of blocksOf(offsets[idx], counts[idx], block)) need.add(b);
          }
        }
      }
    }
  }
  // The header, directories and offset tables: everything a reader parses before any tile.
  if (Number.isFinite(firstTile)) for (const b of blocksOf(0, firstTile, block)) need.add(b);

  const blocks = [...need].sort((a, b) => a - b);
  const bytes = blocks.reduce((n, b) => n + Math.min(block, size - b * block), 0);
  return { url, size, block, blocks, bytes, tiles, bbox, version: head.version };
}
