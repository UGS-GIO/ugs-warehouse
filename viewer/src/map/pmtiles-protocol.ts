// Registers the pmtiles:// protocol so react-map-gl can read PMTiles. Called by the map modules
// rather than main.tsx — importing it there put maplibre in the entry chunk for every view.
//
// The Protocol caches one PMTiles per archive URL in a plain `tiles` Map for the tab's life; cap it
// (well above the real layer count, so normal browsing never evicts) so a runaway can't grow unbounded.
//
// Nothing here knows about offline layers. A downloaded archive is served by the service worker,
// which answers this protocol's range requests out of OPFS, so the reader is unchanged either way.
import maplibregl from "maplibre-gl";
import { Protocol } from "pmtiles";
import { CappedMap } from "@/lib/lru";
import { areaResponse } from "@/offline/area-store";

const PMTILES_ARCHIVE_CAP = 32;
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
  const handler: Handler = async (params, abort) => {
    const kind = params.type === "json" ? "json" : "tile";
    const hit = await areaResponse(params.url, kind, abort.signal, () => network(params, abort));
    return hit ?? network(params, abort);
  };
  maplibregl.addProtocol("pmtiles", handler);
}
