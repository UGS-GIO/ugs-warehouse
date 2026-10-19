// What the persistent map should draw, and the helpers both halves of it need. Split out so the
// provider can live in the entry chunk while maplibre stays behind a lazy import.
import { validBbox } from "./map-model";
import { hasFootprint, isServingTopic, pmtilesLink, rendersOf, type StacDoc } from "@/stac";

// `rowIds`: the tile feature ids are the table's `feature_id`, so a clicked feature can page the
// table to its row. Off for tiles built without them (a publication layer).
export type PreviewSpec =
  | { kind: "vector"; item: StacDoc; pmHref: string; sourceLayer: string; rowIds?: boolean }
  | { kind: "cog"; item: StacDoc; href: string; unitsHref?: string }
  | { kind: "rasterpm"; item: StacDoc; href: string; unitsHref?: string }
  | { kind: "footprint"; item: StacDoc; geometry: GeoJSON.Geometry }
  | null;

export type Renders = ReturnType<typeof rendersOf>;

export const specItemId = (s: PreviewSpec): string => (s ? String(s.item.id ?? "") : "");

// The vector preview of an item with a PMTiles link: its archive and tile layer, and `rowIds` only
// for a serving topic (isServingTopic). Without that stamp the safe default holds: no row join.
export function vectorSpecOf(item: StacDoc): PreviewSpec {
  const pm = pmtilesLink(item);
  return pm
    ? { kind: "vector", item, pmHref: pm.href, sourceLayer: pm["pmtiles:layers"]?.[0] ?? String(item.id ?? ""),
        rowIds: isServingTopic(item) }
    : null;
}

// Whether a clicked tile feature's id names its table row (see `rowIds`).
export const joinsRows = (s: PreviewSpec): boolean => s?.kind === "vector" && s.rowIds === true;

const bboxPolygon = (b: number[]): GeoJSON.Polygon => {
  const [w, s, e, n] = b;
  return { type: "Polygon", coordinates: [[[w, s], [e, s], [e, n], [w, n], [w, s]]] };
};

/** For an item with no previewable file: draw its geometry, else its bbox outline, else nothing.
 *  The statewide placeholder extent is not a footprint, so it draws nothing. */
export function footprintSpecOf(item: StacDoc): PreviewSpec {
  if (!hasFootprint(item)) return null;
  const b = validBbox(item.bbox);
  const geometry = (item.geometry as GeoJSON.Geometry | null | undefined) ?? (b ? bboxPolygon(b) : null);
  return geometry ? { kind: "footprint", item, geometry } : null;
}
