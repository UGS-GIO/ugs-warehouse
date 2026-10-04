import * as maplibregl from "maplibre-gl";

// cog:// protocol registered once, lazily — pulls geotiff.js only when a COG is first viewed.
// Shared by the item-detail COG map (CogMap) and the multi-layer overlay map (ItemMap), so both
// resolve `cog://<href>` raster sources against the same registration.
let cogReady: Promise<void> | null = null;
export const ensureCogProtocol = (): Promise<void> =>
  (cogReady ??= import("@geomatico/maplibre-cog-protocol").then(({ cogProtocol }) => {
    maplibregl.addProtocol("cog", cogProtocol);
  }));
