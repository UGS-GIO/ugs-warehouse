// What the persistent map should draw, and the helpers both halves of it need. Split out so the
// provider can live in the entry chunk while maplibre stays behind a lazy import.
import { validBbox } from "./map-model";
import { rendersOf, type StacDoc } from "./stac";

export type PreviewSpec =
  | { kind: "vector"; item: StacDoc; pmHref: string; sourceLayer: string }
  | { kind: "cog"; item: StacDoc; href: string }
  | { kind: "rasterpm"; item: StacDoc; href: string }
  | { kind: "footprint"; item: StacDoc; geometry: GeoJSON.Geometry }
  | null;

export type Renders = ReturnType<typeof rendersOf>;

export const specItemId = (s: PreviewSpec): string => (s ? String(s.item.id ?? "") : "");

export const bboxPolygon = (b: number[]): GeoJSON.Polygon => {
  const [w, s, e, n] = b;
  return { type: "Polygon", coordinates: [[[w, s], [e, s], [e, n], [w, n], [w, s]]] };
};

/** For an item with no previewable file: draw its geometry, else its bbox outline, else nothing. */
export function footprintSpecOf(item: StacDoc): PreviewSpec {
  const b = validBbox(item.bbox);
  const geometry = (item.geometry as GeoJSON.Geometry | null | undefined) ?? (b ? bboxPolygon(b) : null);
  return geometry ? { kind: "footprint", item, geometry } : null;
}
