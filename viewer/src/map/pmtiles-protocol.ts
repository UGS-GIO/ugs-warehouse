// Registers the pmtiles:// protocol so react-map-gl can read PMTiles. Called by the map modules
// rather than main.tsx — importing it there put maplibre in the entry chunk for every view.
//
// The Protocol caches one PMTiles per archive URL in a plain `tiles` Map for the tab's life; cap it
// (well above the real layer count, so normal browsing never evicts) so a runaway can't grow unbounded.
//
// Nothing here knows about offline layers. A downloaded archive is served by the service worker,
// which answers this protocol's range requests out of OPFS, so the reader is unchanged either way.
import * as maplibregl from "maplibre-gl";
import { PMTiles, Protocol } from "pmtiles";
import { CappedMap } from "@/lib/lru";
import { areaResponse } from "@/offline/area-store";
import { live } from "@/offline/opfs-name";

const PMTILES_ARCHIVE_CAP = 32;
const VERSION_WAIT_MS = 3000;
let registered = false;

export function ensurePmtilesProtocol(): void {
  if (registered) return;
  registered = true;
  const protocol = new Protocol();
  protocol.tiles = new CappedMap(PMTILES_ARCHIVE_CAP);
  // Saved areas answer first (offline/area.ts); anything they don't hold goes to the archive as
  // before. A layer with no saved area never touches the disk.
  type Handler = Parameters<typeof maplibregl.addProtocol>[1];
  const network: Handler = (params, abort) => protocol.tile(params, abort);
  // The published archive's version (its ETag), asked once per archive, for areaResponse.
  const versions = new CappedMap<string, Promise<string | undefined>>(PMTILES_ARCHIVE_CAP);
  const liveVersion = (archive: string) => {
    let v = versions.get(archive);
    if (!v) {
      // Capped: on a dead hotspot the browser still reads online and the request hangs, and every
      // saved tile waits on this answer. Unanswered in time counts as unknown: the saved tiles draw.
      const timeout = new Promise<undefined>((resolve) => { setTimeout(() => resolve(undefined), VERSION_WAIT_MS); });
      v = Promise.race([new PMTiles(live(archive)).getHeader().then((h) => h.etag), timeout]);
      // A failure is asked again (offline now is not offline for good); a timeout is not, or every
      // tile would open another hanging request.
      v.catch(() => versions.delete(archive));
      versions.set(archive, v);
    }
    return v;
  };
  const handler: Handler = async (params, abort) => {
    const kind = params.type === "json" ? "json" : "tile";
    const hit = await areaResponse(params.url, kind, abort.signal, () => network(params, abort), liveVersion);
    return hit ?? network(params, abort);
  };
  maplibregl.addProtocol("pmtiles", handler);
}
