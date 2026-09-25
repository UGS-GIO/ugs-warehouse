// Saved PMTiles areas on the device: their directories, meta.json, listing, deleting, and
// answering the pmtiles protocol from them. Apart from area.ts (planning and saving, which needs
// maplibre's projection) because the offline store and the Offline data page load this, and they
// have no map.
import { type Bbox, bboxesOf, finished, folderSize, isBbox, isDirectory, isRecord, optionalNumber, optionalString, prune } from "./guards";
import { Compression } from "pmtiles";
import { fileNameFor } from "./opfs-name";

export type AreaMeta = {
  tilejson: { tiles: string[]; minzoom: number; maxzoom: number; bounds: Bbox };
  compression: Compression;   // Compression.None or Compression.Gzip (area.ts refuses others)
  /** The archive version the tiles were cut from, the areas saved, and when (for update checks). */
  version?: string;
  bboxes?: Bbox[];
  savedAt?: number;
  /** Tiles and bytes on disk, so listing needs no walk of the tiles (absent on older saves). */
  tiles?: number;
  bytes?: number;
};

const AREAS = "areas";
/** An archive's folder, holding a folder per version of it (guards.ts). */
export async function areaDir(url: string, create: boolean): Promise<FileSystemDirectoryHandle | null> {
  try {
    const root = await navigator.storage.getDirectory();
    const areas = await root.getDirectoryHandle(AREAS, { create });
    return await areas.getDirectoryHandle(fileNameFor(url), { create });
  } catch {
    return null;
  }
}

export const tileName = (z: number, x: number, y: number) => `${z}-${x}-${y}`;

export async function writeFile(dir: FileSystemDirectoryHandle, name: string, data: BlobPart) {
  const out = await (await dir.getFileHandle(name, { create: true })).createWritable();
  await out.write(data);
  await out.close();
}

/** A saved area's meta.json, checked field by field; null when missing or not one. */
export function parseAreaMeta(v: unknown): AreaMeta | null {
  if (!isRecord(v) || !isRecord(v.tilejson)) return null;
  const { tiles, minzoom, maxzoom, bounds } = v.tilejson;
  const compression = v.compression === Compression.None || v.compression === Compression.Gzip ? v.compression : undefined;
  if (!Array.isArray(tiles) || !tiles.every((t) => typeof t === "string") || !isBbox(bounds)
    || typeof minzoom !== "number" || typeof maxzoom !== "number" || compression === undefined) return null;
  return {
    tilejson: { tiles, minzoom, maxzoom, bounds },
    compression,
    version: optionalString(v.version),
    bboxes: bboxesOf(v.bboxes),
    savedAt: optionalNumber(v.savedAt),
    tiles: optionalNumber(v.tiles),
    bytes: optionalNumber(v.bytes),
  };
}

/** An archive's finished saved version: its folder and meta, or null. */
async function finishedArea(url: string): Promise<{ dir: FileSystemDirectoryHandle; meta: AreaMeta } | null> {
  const parent = await areaDir(url, false);
  const done = parent && await finished(parent);
  const meta = done && parseAreaMeta(done.meta);
  return done && meta ? { dir: done.dir, meta } : null;
}

/** Forget every tile saved for an archive. */
export async function removeArea(url: string): Promise<void> {
  stored.delete(url);
  const root = await navigator.storage.getDirectory().catch(() => null);
  const areas = await root?.getDirectoryHandle(AREAS).catch(() => null);
  await areas?.removeEntry(fileNameFor(url), { recursive: true }).catch(() => {});
}

// ---- serving ----

/** Archives with a saved area, so the protocol only looks on disk for layers that have one. */
const stored = new Set<string>();
// Read from disk on the protocol's first request rather than at startup: a map that mounts first
// would otherwise ask for tiles before the set is filled and miss a saved area when offline.
let loaded: Promise<unknown> | null = null;
export const hasArea = (url: string) => stored.has(url);
export const markStored = (url: string) => { stored.add(url); };

export type StoredArea = { url: string; tiles: number; bytes: number; version?: string; bboxes: Bbox[]; savedAt?: number };

export async function loadStoredAreas(): Promise<StoredArea[]> {
  const out: StoredArea[] = [];
  const root = await navigator.storage?.getDirectory?.().catch(() => null);
  const areas = await root?.getDirectoryHandle(AREAS).catch(() => null);
  if (!areas) return out;
  for await (const [name, parent] of areas) {
    if (!isDirectory(parent)) continue;
    const done = await finished(parent);
    const meta = done && parseAreaMeta(done.meta);
    if (!done || !meta) continue;
    const dir = done.dir;
    // Saves record their size; one from before that is counted here instead.
    const { files: tiles, bytes } = meta.tiles !== undefined && meta.bytes !== undefined
      ? { files: meta.tiles, bytes: meta.bytes } : await folderSize(dir);
    const url = decodeURIComponent(name);
    stored.add(url);
    out.push({ url, tiles, bytes, version: meta.version, bboxes: meta.bboxes ?? [], savedAt: meta.savedAt });
  }
  return out;
}

async function inflate(data: ArrayBuffer, compression: Compression): Promise<ArrayBuffer> {
  if (compression === Compression.None) return data;
  const stream = new Blob([data]).stream().pipeThrough(new DecompressionStream("gzip"));
  return new Response(stream).arrayBuffer();
}

/**
 * Answer a pmtiles:// request from a saved area, or null to let the network path handle it.
 * Tiles are stored-first (identical data, and it keeps working offline); the TileJSON is asked of
 * the network first, falling back to the stored copy, so online bounds stay the full archive's.
 */
export async function areaResponse(url: string, kind: "json" | "tile", signal?: AbortSignal,
  network?: () => Promise<{ data: unknown }>, liveVersion?: (archive: string) => Promise<string | undefined>,
): Promise<{ data: unknown } | null> {
  await (loaded ??= loadStoredAreas().catch(() => []));   // once, before the first answer
  const m = kind === "tile" ? /^pmtiles:\/\/(.+)\/(\d+)\/(\d+)\/(\d+)$/.exec(url) : null;
  const archive = kind === "tile" ? m?.[1] : url.slice("pmtiles://".length);
  if (!archive || !stored.has(archive)) return null;
  const saved = await finishedArea(archive);
  if (!saved) return null;
  const { dir, meta } = saved;
  if (kind === "json") {
    if (!network) return { data: meta.tilejson };
    try { return await network(); } catch { return { data: meta.tilejson }; }
  }
  if (!m) return null;
  // A republished archive makes the saved area stale: tiles from both would be mixed on one map, and
  // their feature ids number different rows. Online, the live archive answers every tile; offline
  // (the version cannot be asked), the saved tiles do.
  const now = await liveVersion?.(archive).catch(() => undefined);
  if (now && meta.version && now !== meta.version) return null;
  const [, , z, x, y] = m;
  const file = await dir.getFileHandle(tileName(+z, +x, +y)).then((h) => h.getFile(), () => null);
  if (signal?.aborted) throw new DOMException("aborted", "AbortError");
  if (file) return { data: new Uint8Array(await inflate(await file.arrayBuffer(), meta.compression)) };
  return null;
}

/** Remove the unfinished versions of every archive no queued save is working on (`busy`). */
export async function sweepAreas(busy: Set<string>): Promise<void> {
  const root = await navigator.storage?.getDirectory?.().catch(() => null);
  const areas = await root?.getDirectoryHandle(AREAS).catch(() => null);
  if (!areas) return;
  for await (const [name, parent] of areas) {
    if (isDirectory(parent) && !busy.has(decodeURIComponent(name))) await prune(parent, null);
  }
}
