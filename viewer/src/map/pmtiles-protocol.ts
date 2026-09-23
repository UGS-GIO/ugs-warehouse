// Registers the pmtiles:// protocol so react-map-gl can read PMTiles. Called by the map modules
// rather than main.tsx — importing it there put maplibre in the entry chunk for every view.
//
// The Protocol caches one PMTiles per archive URL in a `tiles` Map for the tab's life; cap it
// (well above the real layer count, so normal browsing never evicts) so a runaway can't grow
// unbounded. Downloaded archives are pinned: the protocol keys by the archive URL, so seeding that
// key with a FileSource over the stored file is the whole of "serve this layer offline", and an
// eviction would put it back on the network with nothing there to answer.
import maplibregl from "maplibre-gl";
import { FileSource, PMTiles, Protocol } from "pmtiles";
import { CappedMap } from "@/lib/lru";
import * as opfs from "@/offline/opfs";

const PMTILES_ARCHIVE_CAP = 32;

/** Archive URLs currently served from OPFS. Read by the eviction guard and the UI. */
const offline = new Set<string>();

let protocol: Protocol | null = null;

export function ensurePmtilesProtocol(): void {
  if (protocol) return;
  protocol = new Protocol();
  protocol.tiles = new CappedMap(PMTILES_ARCHIVE_CAP, (k) => offline.has(k));
  maplibregl.addProtocol("pmtiles", protocol.tile);
}

/** True when `href` is being served from a downloaded file rather than the CDN. */
export const isServedOffline = (href: string): boolean => offline.has(href);

/**
 * Point the protocol at the stored copy of `href`. Returns false when nothing is stored, leaving
 * the archive on its network source.
 */
export async function servePmtilesOffline(href: string): Promise<boolean> {
  // Already on the file: leave the live PMTiles alone. Re-seeding would throw away its header and
  // directory caches under any in-flight tile read. The test is `offline`, not `tiles.has()` — the
  // protocol caches network archives under the same key, and those DO need replacing.
  if (offline.has(href)) return true;
  const file = await opfs.get(href);
  if (!file) return false;
  ensurePmtilesProtocol();
  offline.add(href);                       // add before set(), so the eviction guard sees it
  protocol!.tiles.set(href, new PMTiles(new FileSource(file)));
  return true;
}

/** Put `href` back on its network source (after the stored copy is deleted). */
export function stopServingOffline(href: string): void {
  offline.delete(href);
  protocol?.tiles.delete(href);            // next request rebuilds it against the CDN
}

/**
 * Seed every stored .pmtiles archive into the protocol. Call before the map mounts its sources:
 * a source added first would resolve against the network and fail with no connection.
 */
export async function seedStoredArchives(): Promise<number> {
  const stored = (await opfs.list()).filter((f) => f.url.endsWith(".pmtiles"));
  await Promise.all(stored.map((f) => servePmtilesOffline(f.url)));
  return stored.length;
}
