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
import { Compression, EtagMismatch, findTile, PMTiles, zxyToTileId } from "pmtiles";
import { MercatorCoordinate } from "maplibre-gl";
import { type AreaMeta, areaDir, markStored, readAreaMeta, removeArea, tileName, writeFile } from "./area-store";
import { folderSize, namesIn } from "./guards";
import { FileChangedError } from "./opfs-name";
import type { Bbox } from "./guards";

export type { Bbox } from "./guards";
export type { AreaMeta } from "./area-store";
export type TileRef = { z: number; x: number; y: number; offset: number; length: number };
export type AreaPlan = { url: string; tiles: TileRef[]; bytes: number; meta: AreaMeta; bbox?: Bbox };
// Gzip and uncompressed cover every archive we publish (tippecanoe and Planetiler both gzip MVT;
// raster mosaics are uncompressed images). Anything else is refused rather than stored unreadable.
const SUPPORTED = new Set([Compression.None, Compression.Gzip]);

/** XYZ tiles covering a bbox at one zoom (web mercator, clamped to the valid range). */
export function tilesAt([w, s, e, n]: Bbox, z: number): [number, number][] {
  const count = 2 ** z;
  const tile = (v: number) => Math.min(count - 1, Math.max(0, Math.floor(v * count)));
  // Mercator is undefined at the poles; clamp to the web-mercator limit before projecting.
  const at = (lng: number, lat: number) =>
    MercatorCoordinate.fromLngLat({ lng, lat: Math.max(-85.0511, Math.min(85.0511, lat)) });
  const nw = at(w, n), se = at(e, s);
  const out: [number, number][] = [];
  for (let tx = tile(nw.x); tx <= tile(se.x); tx++) for (let ty = tile(nw.y); ty <= tile(se.y); ty++) out.push([tx, ty]);
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
  try {
    return await planWith(source ?? archiveFor(url), url, bbox);
  } catch (e) {
    // The archive object keeps the header and directories it first read; if the file has been
    // republished since, pmtiles notices on its next read (the ETag moved). Start from a fresh one.
    if (source || !(e instanceof EtagMismatch)) throw e;
    archives.delete(url);
    return planWith(archiveFor(url), url, bbox);
  }
}

async function planWith(p: PMTiles, url: string, bbox: Bbox): Promise<AreaPlan> {
  const h = await p.getHeader();
  // The header's ETag names the version these offsets belong to; no separate request needed.
  const version = h.etag;
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
      version,
    },
    bbox,
  };
}

/**
 * Save a planned area. Tiles already stored (from an overlapping earlier save) are skipped, so
 * saving a neighbouring area only downloads what is new. meta.json is written last: an archive
 * with tiles but no meta is never served, so an interrupted save cannot half-work.
 */
export function saveArea(plan: AreaPlan, onProgress?: (done: number, total: number) => void,
  source?: PMTiles): Promise<void> {
  return (async () => {
    // Tiles cut from an older version of the archive are dropped, not mixed with the new ones.
    const before = await readAreaMeta(plan.url);
    if (before && before.version !== plan.meta.version) await removeArea(plan.url);
    const dir = await areaDir(plan.url, true);
    if (!dir) throw new Error("This browser cannot store data offline.");
    const p = source ?? archiveFor(plan.url);
    const same = before && before.version === plan.meta.version ? before : null;
    let done = 0;
    // One read of the folder, not a lookup per tile.
    const stored = await namesIn(dir);
    for (const t of plan.tiles) {
      const name = tileName(t.z, t.x, t.y);
      if (!stored.has(name)) {
        const got = await p.source.getBytes(t.offset, t.length);
        // Offsets come from the directory of the version planned; another version's bytes there
        // are not this tile.
        if (plan.meta.version && got.etag && got.etag !== plan.meta.version) {
          archives.delete(plan.url);   // so the re-plan reads the new directory
          throw new FileChangedError(plan.url);
        }
        await writeFile(dir, name, got.data);
      }
      onProgress?.(++done, plan.tiles.length);
    }
    // Counted once here, from the folder itself, so the store can list saved areas from meta.json
    // alone. Counting as tiles arrive would miss those an interrupted earlier attempt wrote.
    const { files: tiles, bytes } = await folderSize(dir);
    await writeFile(dir, "meta.json", JSON.stringify({
      ...plan.meta, bboxes: [...(same?.bboxes ?? []), ...(plan.bbox ? [plan.bbox] : [])], savedAt: Date.now(),
      tiles, bytes,
    } satisfies AreaMeta));
    markStored(plan.url);
  })();
}

