// What is at a place on the map, and how each thing there can be kept offline. Pure: the picker
// (whats-here-picker.tsx) renders it, and the tests pin it.
import type { ActiveLayer } from "@/map/map-model";
import { quadAt, quadsInBbox } from "./basemap";
import type { Bbox } from "./area";
import { contains, overlaps, to2d } from "@/lib/bbox";

/** A long press lands on a point; "Save this area" covers the view. */
export type Target = { kind: "area"; bbox: Bbox } | { kind: "point"; lon: number; lat: number; zoom?: number };

export type Here = {
  id: string;
  title: string;
  /** layer = a data layer; map = a published map (plate or mosaic); table = a layer's rows, for saving */
  group: "layer" | "map" | "table";
  /** How it is saved, always cut to the area: "area" for PMTiles tiles, "cog" for COG blocks,
   *  "table" for the GeoParquet row groups (table-area.ts). */
  save: { how: "area" | "cog" | "table"; url: string } | null;
};

/** A row that can be kept offline, with its save method known. */
export type Saveable = Here & { save: NonNullable<Here["save"]> };
export const isSaveable = (h: Here): h is Saveable => h.save !== null;

/** The area a save covers: the view for an area target, the 7.5' quad under the finger for a point. */
export function saveBbox(t: Target): Bbox {
  if (t.kind === "area") return t.bbox;
  const q = quadAt(t.lon, t.lat);
  return [q.west, q.south, q.east, q.north];
}

/** The quads a save covers, for the basemap row and the dialog title. */
export const quadsFor = (t: Target) => (t.kind === "point" ? [quadAt(t.lon, t.lat)] : quadsInBbox(t.bbox));


/**
 * Every layer whose footprint covers the target, sorted data layers first then by title.
 *
 * Everything saves by area, never whole: a PMTiles layer (vector or raster mosaic) keeps its tiles
 * inside the area; a COG plate keeps the blocks a reader needs to draw the area (cog-area.ts),
 * since whole plates run to hundreds of MB and the statewide one to 2.9 GB. A datacube has no
 * offline form yet, so it is listed for showing but not for saving.
 */
const flat = (b: number[]): Bbox | null => to2d(b) ?? null;

export function whatsHere(layers: ActiveLayer[], t: Target): Here[] {
  const hit = (b?: number[]) => {
    const box = b && flat(b);
    return !!box && (t.kind === "point" ? contains(box, t.lon, t.lat) : overlaps(box, t.bbox));
  };
  const rank = { layer: 0, table: 1, map: 2 };
  return layers.filter((l) => hit(l.bbox)).flatMap((l): Here[] => [{
    id: l.id,
    title: l.title,
    group: l.pmHref ? "layer" : "map",
    save: l.pmHref ? { how: "area", url: l.pmHref }
      : l.rasterPmHref ? { how: "area", url: l.rasterPmHref }
      : l.cogHref ? { how: "cog", url: l.cogHref }
      : null,
  }, ...(l.tableHref ? [{
    id: `${l.id}#table`, title: l.title, group: "table" as const, save: { how: "table" as const, url: l.tableHref },
  }] : [])]).sort((a, b) => (a.group === b.group ? a.title.localeCompare(b.title) : rank[a.group] - rank[b.group]));
}
