// The basemap:// protocol: for each tile, a saved archive if there is one, else OpenFreeMap.
// See basemap.ts for the archives and why stored-first.
import { MercatorCoordinate } from "maplibre-gl";
import { PMTiles } from "pmtiles";
import { BASEMAP_BASE, OVERVIEW_MAXZOOM, overviewUrl, quadAt, quadUrl, stateUrl, storedBasemaps } from "./basemap";
import { isRecord } from "./guards";

/** Lon/lat of a web-mercator tile's centre. */
export function tileCenter(z: number, x: number, y: number): [number, number] {
  const n = 2 ** z;
  const { lng, lat } = new MercatorCoordinate((x + 0.5) / n, (y + 0.5) / n).toLngLat();
  return [lng, lat];
}

/** The archive a tile would live in, if we had built it. */
export function archiveFor(z: number, x: number, y: number, base = BASEMAP_BASE): string {
  if (z <= OVERVIEW_MAXZOOM) return overviewUrl(base);
  const [lon, lat] = tileCenter(z, x, y);
  return quadUrl(quadAt(lon, lat).code, base);
}

/**
 * The archive to read a tile from, given what is stored: the statewide file whenever it is saved
 * (it holds every zoom everywhere), otherwise the overview or quad the tile falls in.
 */
export function pickArchive(z: number, x: number, y: number, saved: ReadonlySet<string>,
  base = BASEMAP_BASE): string {
  return saved.has(stateUrl(base)) ? stateUrl(base) : archiveFor(z, x, y, base);
}

const archives = new Map<string, PMTiles>();
const archive = (url: string) => {
  let a = archives.get(url);
  if (!a) archives.set(url, (a = new PMTiles(url)));
  return a;
};

let fallbackTemplate: Promise<string | null> | null = null;
/** OpenFreeMap's current tile URL template. Versioned weekly, so it is read from their TileJSON. */
const fallback = () => (fallbackTemplate ??= fetch("https://tiles.openfreemap.org/planet")
  .then(async (r) => {
    // A bad status or a TileJSON with no template is a failure, not an answer: throw so it is not
    // cached, or one 500 would disable the network basemap until the page reloads.
    if (!r.ok) throw new Error(`OpenFreeMap TileJSON: ${r.status}`);
    const tilejson: unknown = await r.json();
    const tpl = isRecord(tilejson) && Array.isArray(tilejson.tiles) ? tilejson.tiles[0] : undefined;
    if (typeof tpl !== "string") throw new Error("OpenFreeMap TileJSON has no tile template");
    return tpl;
  })
  .catch(() => { fallbackTemplate = null; return null; }));    // retry on the next tile

const EMPTY = { data: new Uint8Array() };

/** MapLibre protocol handler for `basemap://{z}/{x}/{y}`. */
export async function basemapProtocol(
  params: { url: string }, abort: AbortController,
): Promise<{ data: ArrayBuffer | Uint8Array }> {
  const m = /^basemap:\/\/(\d+)\/(\d+)\/(\d+)/.exec(params.url);
  if (!m) throw new Error(`bad basemap URL: ${params.url}`);
  const [z, x, y] = [Number(m[1]), Number(m[2]), Number(m[3])];

  const url = pickArchive(z, x, y, storedBasemaps);
  if (storedBasemaps.has(url)) {
    try {
      const t = await archive(url).getZxy(z, x, y, abort.signal);
      if (t) return { data: new Uint8Array(t.data) };
    } catch { /* fall through to the network basemap */ }
  }

  const tpl = await fallback();
  if (!tpl) return EMPTY;
  try {
    const r = await fetch(tpl.replace("{z}", String(z)).replace("{x}", String(x)).replace("{y}", String(y)),
      { signal: abort.signal });
    return r.ok ? { data: await r.arrayBuffer() } : EMPTY;
  } catch {
    return EMPTY;   // offline and not stored: blank, not an error overlay
  }
}
