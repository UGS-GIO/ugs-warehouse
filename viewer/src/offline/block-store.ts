// Files saved by area as fixed-size blocks: COG plates (cog-area.ts plans them) and GeoParquet
// tables (table-area.ts). One directory per file under cogs/, a block per file, and meta.json last,
// so a half-finished save is ignored. Free of geotiff, so listing what is saved stays light.
import type { Bbox } from "./guards";
import { type BlockMeta, parseBlockMeta } from "./cog-blocks";
import { folderSize, isDirectory, namesIn, readJson } from "./guards";
import { currentVersion, FileChangedError, fileNameFor, versionOf } from "./opfs-name";

const DIR = "cogs";

export type CogPlan = {
  url: string; size: number; block: number; blocks: number[]; bytes: number; tiles: number;
  /** The area saved: a table's queries are clipped to it offline, and an update re-cuts it. */
  bbox?: Bbox;
  /** The file version the blocks were cut from; blocks of two versions must never be mixed. */
  version?: string;
};

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
  return (async () => {
    // Blocks cut from another version of the file are byte ranges of a different file: drop them.
    const old = await cogDir(plan.url, false);
    const prior = old && await readMeta(old);
    if (prior && prior.version !== plan.version) await removeCogArea(plan.url);
    const dir = await cogDir(plan.url, true);
    if (!dir) throw new Error("This browser cannot store data offline.");
    // One read of the folder, not a lookup per block.
    const have = await namesIn(dir);
    const missing = plan.blocks.filter((b) => !have.has(String(b)));
    // Consecutive blocks, as [first, last] index pairs of at most 64 blocks.
    const runs: [number, number][] = [];
    for (const b of missing) {
      const run = runs.at(-1);
      if (run && b === run[1] + 1 && b - run[0] < 64) run[1] = b; else runs.push([b, b]);
    }
    let done = plan.blocks.length - missing.length;
    for (const [first, last] of runs) {
      const start = first * plan.block;
      const end = Math.min(plan.size, (last + 1) * plan.block) - 1;
      const r = await fetch(plan.url, { headers: { range: `bytes=${start}-${end}` } });
      // Blocks are cut from the response by offset; a 200 (the whole file) would put them wrong.
      if (r.status !== 206) throw new Error(`Download failed: the server did not answer a range request (${r.status}).`);
      // The plan's offsets belong to one version of the file; bytes of another are garbage there.
      if (plan.version && versionOf(r.headers) !== plan.version) {
        await r.body?.cancel();
        throw new FileChangedError(plan.url);
      }
      const buf = new Uint8Array(await r.arrayBuffer());
      for (let b = first; b <= last; b++) {
        const out = await (await dir.getFileHandle(String(b), { create: true })).createWritable();
        await out.write(buf.subarray((b - first) * plan.block, (b - first + 1) * plan.block));
        await out.close();
        onProgress?.(++done, plan.blocks.length);
      }
    }
    // Areas accumulate: a second save of the same version adds its blocks and its area to the first.
    const before = await readMeta(dir);
    const bboxes = [...(before?.bboxes ?? []), ...(plan.bbox ? [plan.bbox] : [])];
    // Counted once here, from the folder itself, so the store can list saved areas from meta.json
    // alone. Counting as blocks arrive would miss those an interrupted earlier attempt wrote.
    const { bytes } = await folderSize(dir);
    const meta = await (await dir.getFileHandle("meta.json", { create: true })).createWritable();
    await meta.write(JSON.stringify({
      size: plan.size, block: plan.block, bboxes, version: plan.version, savedAt: Date.now(), bytes,
    } satisfies BlockMeta));
    await meta.close();
  })();
}

async function readMeta(dir: FileSystemDirectoryHandle): Promise<BlockMeta | null> {
  return parseBlockMeta(await readJson(dir, "meta.json"));
}

/** The areas saved of a file stored by blocks, or null when none is. */
export async function savedAreasOf(url: string): Promise<Bbox[] | null> {
  const dir = await cogDir(url, false);
  const meta = dir && await readMeta(dir);
  return meta ? meta.bboxes : null;
}

/** Remove a saved copy cut from a version of `url` other than the live one (`now`, when the
 *  caller has already asked; otherwise one HEAD). */
export async function dropIfStale(url: string, now?: string): Promise<void> {
  const dir = await cogDir(url, false);
  const meta = dir && await readMeta(dir);
  if (!meta) return;
  now ??= await currentVersion(url).catch(() => undefined);
  if (now && meta.version !== now) await removeCogArea(url);
}

/** Forget a saved COG area. */
export async function removeCogArea(url: string): Promise<void> {
  const root = await navigator.storage.getDirectory().catch(() => null);
  const dir = await root?.getDirectoryHandle(DIR).catch(() => null);
  await dir?.removeEntry(fileNameFor(url), { recursive: true }).catch(() => {});
}

/** Every COG saved by area, with its stored size. */
export type StoredBlocks = { url: string; bytes: number; version?: string; bboxes: Bbox[]; savedAt?: number };

export async function listCogAreas(): Promise<StoredBlocks[]> {
  const out: StoredBlocks[] = [];
  const root = await navigator.storage?.getDirectory?.().catch(() => null);
  const dir = await root?.getDirectoryHandle(DIR).catch(() => null);
  if (!dir) return out;
  for await (const [name, handle] of dir) {
    if (!isDirectory(handle)) continue;
    const meta = await readMeta(handle);
    if (!meta) continue;
    // Saves record their size; one from before that is counted here instead.
    const bytes = meta.bytes ?? (await folderSize(handle)).bytes;
    out.push({ url: decodeURIComponent(name), bytes, version: meta.version, bboxes: meta.bboxes, savedAt: meta.savedAt });
  }
  return out;
}
