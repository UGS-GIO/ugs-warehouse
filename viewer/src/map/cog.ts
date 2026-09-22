import { COGLayer, type COGLayerProps } from "@developmentseed/deck.gl-geotiff";
import { epsgResolver, parseWkt } from "@developmentseed/proj";
import { wkt3857 } from "./proj-3857";

// COGs render as deck.gl layers on the shared interleaved MapboxOverlay (see zarr/zarr-overlay), which
// replaces the old @geomatico/maplibre-cog-protocol `cog://` raster source. Shared by the item-detail
// preview (CogMap) and the multi-layer overlay map (ItemMap): both build a COGLayer per href here.
//
// deck.gl-geotiff's COGLayer always reprojects source->3857 and, by default, resolves the source CRS
// from epsg.io over the network. Our pub COGs are always EPSG:3857, so resolve that locally (the same
// definition epsg.io would return), no network round-trip. Module-level const so the layer's prop
// identity stays stable and deck doesn't rebuild (re-fetch) the layer every render.
const cogEpsgResolver = (epsg: number) =>
  epsg === 3857 ? Promise.resolve(parseWkt(wkt3857)) : epsgResolver(epsg);

/** A COG's lon/lat extent [west, south, east, north] (WGS84), for camera-fit. */
export type CogBounds = [number, number, number, number];

/** One COGLayer per href. `beforeId` places it in the MapLibre stack (tray order, under vectors);
 *  `onBounds` reports the COG's extent once its header loads. */
export const buildCogLayer = (opts: {
  id: string;
  href: string;
  beforeId?: string;
  onBounds?: (b: CogBounds) => void;
}): COGLayer => {
  // `beforeId` is honored by @deck.gl/mapbox's interleaved overlay but isn't declared in COGLayerProps,
  // so widen the props type to carry it (the callback stays contextually typed off COGLayerProps).
  const props: COGLayerProps & { beforeId?: string } = {
    id: opts.id,
    geotiff: opts.href,
    epsgResolver: cogEpsgResolver,
    beforeId: opts.beforeId,
    opacity: 0.9,
    onGeoTIFFLoad: (_gt, { geographicBounds: b }) =>
      opts.onBounds?.([b.west, b.south, b.east, b.north]),
  };
  return new COGLayer(props);
};
