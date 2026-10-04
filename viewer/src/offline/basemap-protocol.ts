// The basemap:// protocol: for each tile, a saved archive if there is one, else our statewide file.
// See basemap.ts for the archives and why stored-first.
import { MercatorCoordinate } from "maplibre-gl";
import { PMTiles } from "pmtiles";
import { BASEMAP_BASE, OVERVIEW_MAXZOOM, overviewUrl, quadAt, quadUrl, stateUrl, storedBasemaps } from "./basemap";

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

// A new one each time: MapLibre transfers the buffer to its worker, which empties it for reuse.
const empty = () => ({ data: new Uint8Array() });

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
    } catch { /* fall through to the network */ }
  }

  try {
    const t = await archive(stateUrl()).getZxy(z, x, y, abort.signal);
    return t ? { data: new Uint8Array(t.data) } : empty();
  } catch {
    return empty();   // offline and not stored: blank, not an error overlay
  }
}
