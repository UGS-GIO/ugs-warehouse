// Identify under a geologic-map raster: the raster Item's `units` PMTiles loads as an invisible
// vector layer, and a click on the raster queries it. Framework-free so the click logic is tested
// without a map.
import { catalogItemHref, pubOfEntry, type StacDoc } from "@/stac";
import { identifyIds, validBbox } from "./map-model";

// The tile layer inside every `units` PMTiles.
export const UNITS_SOURCE_LAYER = "units";

export type UnitInfo = {
  seriesId: string; code: string; name?: string; age?: string; description?: string; rank: number;
};

export type IdentifyResult =
  | { kind: "unit"; unit: UnitInfo }
  | { kind: "scan-only"; pubTitle?: string };

// The `units` asset href of a plate or mosaic Item, when it carries one.
export const unitsHrefOf = (d: StacDoc | undefined): string | undefined => {
  const a = d?.assets?.units;
  return a?.type?.includes("pmtiles") && a.href ? a.href : undefined;
};

// A fill that is queryable but draws nothing. Not exactly 0: a fully transparent fill is not
// guaranteed to be returned by queryRenderedFeatures, and 0.001 is invisible.
export const IDENTIFY_PAINT = { "fill-color": "#000000", "fill-opacity": 0.001 } as const;

const text = (v: unknown): string | undefined => {
  const s = v == null ? "" : String(v).trim();
  return s || undefined;
};

type Feat = { properties?: Record<string, unknown> | null };

// Where mosaic quads overlap, several features hit one point; the highest rank is the one drawn on
// top in the mosaic.
export function topUnit(features: readonly Feat[]): UnitInfo | undefined {
  let best: UnitInfo | undefined;
  for (const f of features) {
    const p = f.properties ?? {};
    const code = text(p.unit_code);
    if (!code) continue;
    const r = Number(p.rank);
    const rank = Number.isFinite(r) ? r : -1;
    if (best && rank <= best.rank) continue;
    best = { seriesId: text(p.series_id) ?? "", code, name: text(p.unit_name), age: text(p.age),
             description: text(p.description), rank };
  }
  return best;
}

export const inBbox = (lng: number, lat: number, bbox: number[] | undefined): boolean => {
  const b = validBbox(bbox);
  return !!b && lng >= b[0] && lng <= b[2] && lat >= b[1] && lat <= b[3];
};

// One raster on the map or in the preview, front first.
export type IdentifyTarget = {
  slug: string;
  hasUnits: boolean;       // false: no identify layer was added (the pub has no unit data)
  bbox?: number[];         // the raster's footprint, for the scan-only answer
  pubTitle?: string;       // the covering publication, when known
};

type MapLike = {
  getLayer(id: string): unknown;
  queryRenderedFeatures(geometry: unknown, opts: { layers: string[] }): Feat[];
};

// The unit under a click on the front-most raster that has one; else, if the point is on a raster's
// footprint, the scan-only answer; else null (not on a raster).
export function identifyAt(
  map: MapLike, point: { x: number; y: number }, lngLat: { lng: number; lat: number },
  targets: IdentifyTarget[],
): IdentifyResult | null {
  for (const t of targets) {
    const id = identifyIds(t.slug).layer;
    if (!t.hasUnits || !map.getLayer(id)) continue;
    const unit = topUnit(map.queryRenderedFeatures([point.x, point.y], { layers: [id] }));
    if (unit) return { kind: "unit", unit };
  }
  const on = targets.find((t) => inBbox(lngLat.lng, lngLat.lat, t.bbox));
  return on ? { kind: "scan-only", pubTitle: on.pubTitle } : null;
}

// What a map click identifies. A data layer's feature wins and keeps its own behavior. A coverage
// rectangle covers every raster in the collection, so it only yields to a unit actually found; a
// scan-only answer there falls through to its "Open item" popup.
export function identifyClick(
  feature: { layer: { id: string } } | undefined, identify: () => IdentifyResult | null,
): IdentifyResult | null {
  if (feature && feature.layer.id !== COVERAGE_LAYER) return null;
  const hit = identify();
  return feature && hit?.kind === "scan-only" ? null : hit;
}
export const COVERAGE_LAYER = "coverage-fill";

type Entry = { href: string; data?: StacDoc };

// An in-app catalog page: the `c` and `i` search params.
export type PageRef = { c: string; i: string };
export type PubContext = { title: string; page?: PageRef; dmu?: PageRef };

const DMU_TITLE = /Description\s*Of\s*Map\s*Units/i;

// The catalog page of an entry, from its STAC self href.
const pageRef = (stacHref: string): PageRef | undefined => {
  const q = new URLSearchParams(catalogItemHref(stacHref));
  const c = q.get("c"), i = q.get("i");
  return c && i ? { c, i } : undefined;
};

// A tile's `series_id` resolved against the root index: the publication's title and page, and its
// Description of Map Units table Item when the index lists one. The id stands in for an unknown title.
export function pubContext(seriesId: string, entries: readonly Entry[]): PubContext {
  let title = seriesId;
  let page: PageRef | undefined;
  let dmu: PageRef | undefined;
  for (const e of entries) {
    const d = e.data;
    const props = d?.properties as Record<string, unknown> | undefined;
    const pub = pubOfEntry(d);
    if (pub?.id === seriesId) {
      if (title === seriesId) title = pub.title;
      if (!dmu && props?.["ugs:layer_kind"] === "table" && DMU_TITLE.test(String(props?.title ?? ""))) {
        dmu = pageRef(e.href);
      }
    } else if (d?.id === seriesId && /collection\.json/.test(e.href)) {
      title = String(props?.title ?? seriesId);
      page = pageRef(e.href);
    }
  }
  return { title, page, dmu };
}
