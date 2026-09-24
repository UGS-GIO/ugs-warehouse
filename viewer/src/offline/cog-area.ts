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
import { fromUrl, type GeoTIFF } from "geotiff";
import type { Bbox } from "./area";
import { track } from "./in-flight";
import { fileNameFor } from "./opfs-name";

export { assemble } from "./cog-blocks";

/** Must equal the block size the COG reader requests in (geomatico's CogReader, 65536). */
export const COG_BLOCK = 65536;
const DIR = "cogs";

export type CogPlan = {
  url: string; size: number; block: number; blocks: number[]; bytes: number; tiles: number;
  /** The area saved, recorded for a table (table-area.ts), whose queries are clipped to it offline. */
  bbox?: Bbox;
};

const merc = (lon: number, lat: number): [number, number] => [
  (lon * 20037508.342789244) / 180,
  Math.log(Math.tan(Math.PI / 4 + (Math.max(-85.0511, Math.min(85.0511, lat)) * Math.PI) / 360)) * 6378137,
];

async function fileSize(url: string): Promise<number> {
  const r = await fetch(url, { method: "HEAD" });
  if (!r.ok) throw new Error(`${r.status}`);
  return Number(r.headers.get("content-length"));
}

/** Blocks covering [start, start + length). */
function blocksOf(start: number, length: number, block: number): number[] {
  const out: number[] = [];
  for (let b = Math.floor(start / block); b <= Math.floor((start + length - 1) / block); b++) out.push(b);
  return out;
}

/**
 * The blocks of a COG needed to draw `bbox` at every overview level, priced exactly. Reads only
 * the directory at the front of the file; no tile is downloaded to plan.
 */
export async function planCogArea(url: string, bbox: Bbox,
  opts: { tiff?: GeoTIFF; size?: number; block?: number } = {}): Promise<CogPlan> {
  const block = opts.block ?? COG_BLOCK;
  // Read in the reader's own block size, so planning warms the same blocks drawing will ask for.
  // geotiff.js accepts blockSize at runtime (remote.js maybeWrapInBlockedSource); its .d.ts lags.
  const tiff = opts.tiff ?? await fromUrl(url, { blockSize: COG_BLOCK } as Parameters<typeof fromUrl>[1]);
  const size = opts.size ?? await fileSize(url);
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
    const offsets = Array.from((await fd.loadValue("TileOffsets")) as ArrayLike<number>);
    const counts = Array.from((await fd.loadValue("TileByteCounts")) as ArrayLike<number>);
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
    const planes = fd.getValue("PlanarConfiguration") === 2 ? (fd.getValue("SamplesPerPixel") as number) : 1;
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
  return { url, size, block, blocks, bytes, tiles };
}

async function cogDir(url: string, create: boolean) {
  try {
    const root = await navigator.storage.getDirectory();
    return await (await root.getDirectoryHandle(DIR, { create })).getDirectoryHandle(fileNameFor(url), { create });
  } catch {
    return null;
  }
}

/**
 * Save a planned area of a COG. Consecutive blocks are fetched as one range request (capped so a
 * single response stays small), each block written as its own file; blocks already stored from an
 * earlier, overlapping save are skipped. meta.json goes last, so a half-finished save is ignored.
 */
export function saveCogArea(plan: CogPlan, onProgress?: (done: number, total: number) => void): Promise<void> {
  return track((async () => {
    const dir = await cogDir(plan.url, true);
    if (!dir) throw new Error("This browser cannot store data offline.");
    const missing: number[] = [];
    for (const b of plan.blocks) {
      if (!(await dir.getFileHandle(String(b)).then(() => true, () => false))) missing.push(b);
    }
    const runs: number[][] = [];
    for (const b of missing) {
      const run = runs.at(-1);
      if (run && b === run.at(-1)! + 1 && run.length < 64) run.push(b); else runs.push([b]);
    }
    let done = plan.blocks.length - missing.length;
    for (const run of runs) {
      const start = run[0] * plan.block;
      const end = Math.min(plan.size, (run.at(-1)! + 1) * plan.block) - 1;
      const r = await fetch(plan.url, { headers: { range: `bytes=${start}-${end}` } });
      if (!r.ok) throw new Error(`Download failed: ${r.status}`);
      const buf = new Uint8Array(await r.arrayBuffer());
      for (const b of run) {
        const out = await (await dir.getFileHandle(String(b), { create: true })).createWritable();
        await out.write(buf.subarray((b - run[0]) * plan.block, (b - run[0] + 1) * plan.block));
        await out.close();
        onProgress?.(++done, plan.blocks.length);
      }
    }
    // Areas accumulate: a second save of the same file adds its blocks and its area to the first.
    const before = await readMeta(dir);
    const bboxes = [...(before?.bboxes ?? []), ...(plan.bbox ? [plan.bbox] : [])];
    const meta = await (await dir.getFileHandle("meta.json", { create: true })).createWritable();
    await meta.write(JSON.stringify({ size: plan.size, block: plan.block, bboxes }));
    await meta.close();
  })());
}

type Meta = { size: number; block: number; bboxes?: Bbox[] };

async function readMeta(dir: FileSystemDirectoryHandle): Promise<Meta | null> {
  return dir.getFileHandle("meta.json").then((h) => h.getFile()).then((f) => f.text())
    .then((t) => JSON.parse(t) as Meta).catch(() => null);
}

/** The areas saved of a file stored by blocks, or null when none is. */
export async function savedAreasOf(url: string): Promise<Bbox[] | null> {
  const dir = await cogDir(url, false);
  const meta = dir && await readMeta(dir);
  return meta ? meta.bboxes ?? [] : null;
}

/** Forget a saved COG area. */
export async function removeCogArea(url: string): Promise<void> {
  const root = await navigator.storage.getDirectory().catch(() => null);
  const dir = await root?.getDirectoryHandle(DIR).catch(() => null);
  await dir?.removeEntry(fileNameFor(url), { recursive: true }).catch(() => {});
}

/** Every COG saved by area, with its stored size. */
export async function listCogAreas(): Promise<{ url: string; bytes: number }[]> {
  const out: { url: string; bytes: number }[] = [];
  const root = await navigator.storage?.getDirectory?.().catch(() => null);
  const dir = await root?.getDirectoryHandle(DIR).catch(() => null);
  if (!dir) return out;
  for await (const [name, handle] of dir) {
    if (handle.kind !== "directory") continue;
    let bytes = 0;
    let complete = false;
    for await (const [n, f] of handle as FileSystemDirectoryHandle) {
      if (n === "meta.json") complete = true;
      else if (f.kind === "file") bytes += (await (f as FileSystemFileHandle).getFile()).size;
    }
    if (complete) out.push({ url: decodeURIComponent(name), bytes });
  }
  return out;
}
