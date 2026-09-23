// Save part of a PMTiles layer for offline use: just the tiles inside an area, cut on the device.
//
// No server step and nothing pre-cut: the area is read straight out of the layer's published
// archive with range requests, so it works for every layer, including ones added tomorrow, and
// is never staler than what is published. Pricing is exact and costs no tile downloads: a PMTiles
// directory records every tile's byte length, so walking it (a few small range reads, cached by
// the library) gives the size before anyone commits to saving.
//
// Stored per archive under areas/<encoded archive URL>/: meta.json (the TileJSON the pmtiles
// protocol would have built, plus the tile compression) and one file per tile, raw as published.
// Raw keeps the stored size equal to the quoted size; the protocol decompresses when it serves.
import { findTile, PMTiles, zxyToTileId } from "pmtiles";
import { fileNameFor } from "./opfs-name";
import { track } from "./in-flight";

export type Bbox = [number, number, number, number];
export type TileRef = { z: number; x: number; y: number; offset: number; length: number };
export type AreaPlan = { url: string; tiles: TileRef[]; bytes: number; meta: AreaMeta };
export type AreaMeta = {
  tilejson: { tiles: string[]; minzoom: number; maxzoom: number; bounds: Bbox };
  compression: number;     // pmtiles Compression: 1 none, 2 gzip
};

const AREAS = "areas";
// Gzip and uncompressed cover every archive we publish (tippecanoe and Planetiler both gzip MVT;
// raster mosaics are uncompressed images). Anything else is refused rather than stored unreadable.
const SUPPORTED = new Set([1, 2]);

/** XYZ tiles covering a bbox at one zoom (web mercator, clamped to the valid range). */
export function tilesAt([w, s, e, n]: Bbox, z: number): [number, number][] {
  const count = 2 ** z;
  const x = (lon: number) => Math.min(count - 1, Math.max(0, Math.floor(((lon + 180) / 360) * count)));
  const y = (lat: number) => {
    const r = (Math.max(-85.0511, Math.min(85.0511, lat)) * Math.PI) / 180;
    return Math.min(count - 1, Math.max(0, Math.floor(((1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2) * count)));
  };
  const out: [number, number][] = [];
  for (let tx = x(w); tx <= x(e); tx++) for (let ty = y(n); ty <= y(s); ty++) out.push([tx, ty]);
  return out;
}

const archives = new Map<string, PMTiles>();
const archiveFor = (url: string) => {
  let a = archives.get(url);
  if (!a) archives.set(url, (a = new PMTiles(url)));
  return a;
};

/** Where tile z/x/y lives in the archive, or null when the archive has no such tile. */
async function locate(p: PMTiles, h: Awaited<ReturnType<PMTiles["getHeader"]>>, z: number, x: number, y: number) {
  const id = zxyToTileId(z, x, y);
  let offset = h.rootDirectoryOffset;
  let length = h.rootDirectoryLength;
  for (let depth = 0; depth <= 3; depth++) {
    const dir = await p.cache.getDirectory(p.source, offset, length, h);
    const entry = findTile(dir, id);
    if (!entry) return null;
    if (entry.runLength > 0) return { offset: h.tileDataOffset + entry.offset, length: entry.length };
    offset = h.leafDirectoryOffset + entry.offset;   // a leaf directory: descend
    length = entry.length;
  }
  return null;
}

/** Every tile of `url` inside `bbox`, with exact byte sizes, read from the directory alone. */
export async function planArea(url: string, bbox: Bbox, source?: PMTiles): Promise<AreaPlan> {
  const p = source ?? archiveFor(url);
  const h = await p.getHeader();
  if (!SUPPORTED.has(h.tileCompression)) {
    throw new Error(`This layer's tiles use a compression this browser store does not support (${h.tileCompression}).`);
  }
  const tiles: TileRef[] = [];
  for (let z = h.minZoom; z <= h.maxZoom; z++) {
    for (const [x, y] of tilesAt(bbox, z)) {
      const at = await locate(p, h, z, x, y);
      if (at) tiles.push({ z, x, y, ...at });
    }
  }
  return {
    url,
    tiles,
    bytes: tiles.reduce((n, t) => n + t.length, 0),
    meta: {
      tilejson: {
        tiles: [`pmtiles://${url}/{z}/{x}/{y}`], minzoom: h.minZoom, maxzoom: h.maxZoom,
        bounds: [h.minLon, h.minLat, h.maxLon, h.maxLat],
      },
      compression: h.tileCompression,
    },
  };
}

async function areaDir(url: string, create: boolean): Promise<FileSystemDirectoryHandle | null> {
  try {
    const root = await navigator.storage.getDirectory();
    const areas = await root.getDirectoryHandle(AREAS, { create });
    return await areas.getDirectoryHandle(fileNameFor(url), { create });
  } catch {
    return null;
  }
}

const tileName = (z: number, x: number, y: number) => `${z}-${x}-${y}`;

async function write(dir: FileSystemDirectoryHandle, name: string, data: BlobPart) {
  const out = await (await dir.getFileHandle(name, { create: true })).createWritable();
  await out.write(data);
  await out.close();
}

/**
 * Save a planned area. Tiles already stored (from an overlapping earlier save) are skipped, so
 * saving a neighbouring area only downloads what is new. meta.json is written last: an archive
 * with tiles but no meta is never served, so an interrupted save cannot half-work.
 */
export function saveArea(plan: AreaPlan, onProgress?: (done: number, total: number) => void,
  source?: PMTiles): Promise<void> {
  return track((async () => {
    const dir = await areaDir(plan.url, true);
    if (!dir) throw new Error("This browser cannot store data offline.");
    const p = source ?? archiveFor(plan.url);
    let done = 0;
    for (const t of plan.tiles) {
      const name = tileName(t.z, t.x, t.y);
      const have = await dir.getFileHandle(name).then(() => true, () => false);
      if (!have) await write(dir, name, (await p.source.getBytes(t.offset, t.length)).data);
      onProgress?.(++done, plan.tiles.length);
    }
    await write(dir, "meta.json", JSON.stringify(plan.meta));
    stored.add(plan.url);
  })());
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

export async function loadStoredAreas(): Promise<{ url: string; tiles: number; bytes: number }[]> {
  const out: { url: string; tiles: number; bytes: number }[] = [];
  const root = await navigator.storage?.getDirectory?.().catch(() => null);
  const areas = await root?.getDirectoryHandle(AREAS).catch(() => null);
  if (!areas) return out;
  for await (const [name, handle] of areas) {
    if (handle.kind !== "directory") continue;
    const dir = handle as FileSystemDirectoryHandle;
    if (!(await dir.getFileHandle("meta.json").then(() => true, () => false))) continue;
    let tiles = 0;
    let bytes = 0;
    for await (const [n, h] of dir) {
      if (n === "meta.json" || h.kind !== "file") continue;
      tiles++;
      bytes += (await (h as FileSystemFileHandle).getFile()).size;
    }
    const url = decodeURIComponent(name);
    stored.add(url);
    out.push({ url, tiles, bytes });
  }
  return out;
}

async function inflate(data: ArrayBuffer, compression: number): Promise<ArrayBuffer> {
  if (compression === 1) return data;
  const stream = new Response(data).body!.pipeThrough(new DecompressionStream("gzip"));
  return new Response(stream).arrayBuffer();
}

/**
 * Answer a pmtiles:// request from a saved area, or null to let the network path handle it.
 * Tiles are stored-first (identical data, and it keeps working offline); the TileJSON is asked of
 * the network first, falling back to the stored copy, so online bounds stay the full archive's.
 */
export async function areaResponse(url: string, kind: "json" | "tile", signal?: AbortSignal,
  network?: () => Promise<{ data: unknown }>): Promise<{ data: unknown } | null> {
  await (loaded ??= loadStoredAreas().catch(() => []));   // once, before the first answer
  const m = kind === "tile" ? /^pmtiles:\/\/(.+)\/(\d+)\/(\d+)\/(\d+)$/.exec(url) : null;
  const archive = kind === "tile" ? m?.[1] : url.slice("pmtiles://".length);
  if (!archive || !stored.has(archive)) return null;
  const dir = await areaDir(archive, false);
  if (!dir) return null;
  const meta = JSON.parse(await (await (await dir.getFileHandle("meta.json")).getFile()).text()) as AreaMeta;
  if (kind === "json") {
    try { return await network!(); } catch { return { data: meta.tilejson }; }
  }
  const [, , z, x, y] = m!;
  const file = await dir.getFileHandle(tileName(+z, +x, +y)).then((h) => h.getFile(), () => null);
  if (signal?.aborted) throw new DOMException("aborted", "AbortError");
  if (file) return { data: new Uint8Array(await inflate(await file.arrayBuffer(), meta.compression)) };
  return null;
}
