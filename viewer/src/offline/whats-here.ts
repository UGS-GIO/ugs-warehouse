// What is at a place on the map, and how each thing there can be kept offline. Pure: the picker
// (whats-here-picker.tsx) renders it, and the tests pin it.
import type { ActiveLayer } from "@/map/map-model";
import { quadAt, quadsInBbox } from "./basemap";
import type { Bbox } from "./area";

/** A long press lands on a point; "Save this area" covers the view. */
export type Target = { kind: "area"; bbox: Bbox } | { kind: "point"; lon: number; lat: number };

export type Here = {
  id: string;
  title: string;
  /** vector = a data layer; map = a published map (plate or mosaic); only draws for show */
  group: "layer" | "map";
  /** How it is saved: "area" cuts the tiles inside the area, "file" keeps the whole file. */
  save: { how: "area" | "file"; url: string } | null;
};

/** The area a save covers: the view for an area target, the 7.5' quad under the finger for a point. */
export function saveBbox(t: Target): Bbox {
  if (t.kind === "area") return t.bbox;
  const q = quadAt(t.lon, t.lat);
  return [q.west, q.south, q.east, q.north];
}

/** The quads a save covers, for the basemap row and the dialog title. */
export const quadsFor = (t: Target) => (t.kind === "point" ? [quadAt(t.lon, t.lat)] : quadsInBbox(t.bbox));

const overlaps = (b: number[], [w, s, e, n]: Bbox) => b[0] <= e && b[2] >= w && b[1] <= n && b[3] >= s;
const contains = (b: number[], lon: number, lat: number) => b[0] <= lon && lon <= b[2] && b[1] <= lat && lat <= b[3];

/**
 * Every layer whose footprint covers the target, sorted data layers first then by title.
 *
 * A PMTiles layer (vector or raster mosaic) saves by area: only its tiles inside the area are cut.
 * A COG plate saves whole: it is already one map sheet, and a partial plate would draw holed.
 * A datacube has no offline form yet, so it is listed for showing but not for saving.
 */
export function whatsHere(layers: ActiveLayer[], t: Target): Here[] {
  const hit = (b?: number[]) => !!b && b.length >= 4
    && (t.kind === "point" ? contains(b, t.lon, t.lat) : overlaps(b, t.bbox));
  return layers.filter((l) => hit(l.bbox)).map((l): Here => ({
    id: l.id,
    title: l.title,
    group: l.pmHref ? "layer" : "map",
    save: l.pmHref ? { how: "area", url: l.pmHref }
      : l.rasterPmHref ? { how: "area", url: l.rasterPmHref }
      : l.cogHref ? { how: "file", url: l.cogHref }
      : null,
  })).sort((a, b) => (a.group === b.group ? a.title.localeCompare(b.title) : a.group === "layer" ? -1 : 1));
}
