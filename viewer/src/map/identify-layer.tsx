// A raster's units PMTiles as an invisible, queryable fill. It draws nothing; a click on the raster
// reads it (identifyAt).
import { Layer, Source } from "react-map-gl/maplibre";
import { IDENTIFY_PAINT, UNITS_SOURCE_LAYER } from "./identify";
import { identifyIds } from "./map-model";

export function IdentifyLayer({ slug, href }: { slug: string; href: string }) {
  const ids = identifyIds(slug);
  return (
    <Source id={ids.source} type="vector" url={`pmtiles://${href}`}>
      <Layer id={ids.layer} type="fill" source-layer={UNITS_SOURCE_LAYER} paint={IDENTIFY_PAINT} />
    </Source>
  );
}
