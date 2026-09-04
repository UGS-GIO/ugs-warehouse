// Registers the pmtiles:// protocol so react-map-gl can read PMTiles. Called by the map modules
// rather than main.tsx — importing it there put maplibre in the entry chunk for every view.
//
// The Protocol caches one PMTiles per archive URL in a plain `tiles` Map for the tab's life; cap it
// (well above the real layer count, so normal browsing never evicts) so a runaway can't grow unbounded.
import maplibregl from "maplibre-gl";
import { Protocol } from "pmtiles";
import { CappedMap } from "@/lib/lru";

const PMTILES_ARCHIVE_CAP = 32;
let registered = false;

export function ensurePmtilesProtocol(): void {
  if (registered) return;
  registered = true;
  const protocol = new Protocol();
  protocol.tiles = new CappedMap(PMTILES_ARCHIVE_CAP);
  maplibregl.addProtocol("pmtiles", protocol.tile);
}
