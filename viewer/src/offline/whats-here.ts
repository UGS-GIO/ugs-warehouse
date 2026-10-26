// What is at a place on the map, and how each thing there can be kept offline. Pure: the picker
// (whats-here-picker.tsx) renders it, and the tests pin it.
import type { ActiveLayer } from "@/map/map-model";
import { quadAt, quadsInBbox } from "./basemap";
import type { Bbox } from "./area";
import { contains, overlaps, to2d } from "@/lib/bbox";
import { parseScale } from "@/catalog/item-view";

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
  /** A plate or mosaic's `units` PMTiles, which names the unit at a point. */
  unitsHref?: string;
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
    ...(l.unitsHref ? { unitsHref: l.unitsHref } : {}),
  }, ...(l.tableHref ? [{
    id: `${l.id}#table`, title: l.title, group: "table" as const, save: { how: "table" as const, url: l.tableHref },
  }] : [])]).sort((a, b) => (a.group === b.group ? a.title.localeCompare(b.title) : rank[a.group] - rank[b.group]));
}

/**
 * How a long press checks a row is really under the point, beyond its bbox: a vector layer is
 * asked for its features there, a raster mosaic whether it has a tile there. A plate has only its
 * bbox (the index carries no outline), and a table is only something to save. A datacube covers
 * the whole state, so it says nothing about this place and is left out.
 */
export function probeOf(h: Here): "identify" | "tile" | "bbox" | null {
  if (h.group === "table" || !h.save) return null;
  if (h.save?.how !== "area") return "bbox";
  return h.group === "layer" ? "identify" : "tile";
}

/** Reference layers: boundaries and grids that cover every point, so they are listed apart and
 *  collapsed. Chosen by item id; their STAC items carry nothing that marks them. */
export const REFERENCE_IDS = new Set([
  "enmin_plss_sections", "enmin_plss_townshiprange", "enmin_ut_counties", "enmin_ccus_cbcounty",
  "enmin_ccs_georegions", "enmin_ccs_exclusionzones", "hazards_studyareas",
  "wetlands_subbasin_huc8", "wetlands_subbasin_huc8_ecoregion", "wetlands_watershed_huc12",
  "wetlands_watershed_huc12_ecoregion", "wetlands_wetlandsapp_huc12", "wetlands_plants_huc8",
  "wetlands_ecoregion", "wetlands_plants_ecoregion", "wetlands_assessment_projects",
  "wetlands_wetlands_metadata", "wetlands_riparian_metadata", "wetlands_wetlandsoutline",
]);

export type Unit = { name?: string; symbol?: string; age?: string; description?: string; scale?: number };

// The geologic-unit layers and their fields, from each layer's own schema. GeMS map-unit polygons
// carry only the unit code; their descriptions live in a separate table.
const UNIT_FIELDS: Record<string, { name?: string; symbol: string; age?: string; scale: string | number }> = {
  geolmap_geolunits_500k: { name: "unitname", symbol: "unitsymbol", age: "age", scale: 500000 },
  geolmap_geolunits_gems: { symbol: "mapunit", scale: "scale" },
};
export const isUnitLayer = (id: string) => Object.hasOwn(UNIT_FIELDS, id);

const text = (v: unknown) => (v == null || String(v).trim() === "" ? undefined : String(v).trim());

/** The unit a geologic-unit layer's feature names, or null when the layer is not one. */
export function unitOf(layerId: string, props: Record<string, unknown>): Unit | null {
  const f = isUnitLayer(layerId) ? UNIT_FIELDS[layerId] : undefined;
  const symbol = f && text(props[f.symbol]);
  if (!f || !symbol) return null;
  return { symbol, name: f.name && text(props[f.name]), age: f.age && text(props[f.age]),
           scale: typeof f.scale === "number" ? f.scale : parseScale(props[f.scale]) };
}

/** How far a mapped line can sit from the ground, by the usual ±0.5 mm at map scale, in meters. */
export const toleranceM = (scale: number) => scale * 0.0005;

/** A plain warning matched to a map's scale: none at 1:24,000 or more detailed. */
export function caveatOf(scale?: number): string | null {
  if (!scale || scale <= 24000) return null;
  if (scale <= 100000) return `Boundaries approximate (about ±${Math.round(toleranceM(scale))} m).`;
  return "Generalized statewide map; boundaries can be off by several hundred meters. Check a more detailed map.";
}

/** Eight points `r` meters around a point, to look for another unit within a map's tolerance. */
export function ringAround(lon: number, lat: number, r: number): [number, number][] {
  const dLat = r / 111_320, dLon = r / (111_320 * Math.cos((lat * Math.PI) / 180));
  return Array.from({ length: 8 }, (_, i) => {
    const a = (i * Math.PI) / 4;
    return [lon + dLon * Math.cos(a), lat + dLat * Math.sin(a)];
  });
}

/** Another unit among those found around the point, if any: the point is near a contact. */
export const otherUnit = (at: Unit, around: (Unit | null | undefined)[]): Unit | undefined =>
  around.find((u): u is Unit => !!u && u.symbol !== at.symbol);

// Scales a reader treats as the same detail (7.5', 15', 30'x60' quads); within one, the newer map wins.
const TIERS = [24000, 62500, 100000];
export const tierOf = (scale?: number): number => {
  if (!scale) return TIERS.length + 1;
  const i = TIERS.findIndex((t) => scale <= t);
  return i < 0 ? TIERS.length : i;
};

type Ranked = { scale?: number; year?: number | null };
/** Best map first: the more detailed scale tier, then the newer map, then the finer scale. */
export const mapOrder = (a: Ranked, b: Ranked): number =>
  tierOf(a.scale) - tierOf(b.scale) || (b.year ?? 0) - (a.year ?? 0) || (a.scale ?? Infinity) - (b.scale ?? Infinity);

/** The best of several maps by mapOrder. */
export const bestMap = <T extends Ranked>(xs: T[]): T | undefined => [...xs].sort(mapOrder)[0];
