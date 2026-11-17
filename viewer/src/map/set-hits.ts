// What a click on a publication preview found, as rows to list and pick from. Framework-free, so it
// is tested without a map.
import turfBbox from "@turf/bbox";

import type { HighlightTarget } from "./highlight";
import type { IdentifyResult } from "./identify";
import type { FocusSel } from "./map-model";
import type { PreviewHit } from "./preview-map";

export type ClickedFeature = {
  layerId: string; source: string; sourceLayer?: string; id?: string | number;
  properties: Record<string, unknown> | null; geometry: GeoJSON.Geometry;
};

// An attribute that names one feature. Publication layers are tiled without feature ids, so one of
// these is the only way to find the whole feature again in its tiles.
const ID_FIELD = /^(objectid|object_id|fid|ogc_fid|gid|id|feature_id)$/i;
const isScalar = (v: unknown): v is string | number | boolean =>
  typeof v === "string" || typeof v === "number" || typeof v === "boolean";

// The feature drawn whole from its source, found by its id field, or else by all its attributes when
// one is a measured value (a length or an area, which no other feature shares). Otherwise the clicked
// tile's own geometry: it may stop at a tile edge, but a line whose only attribute is its type does
// not light every line of that type.
export function featureTarget(f: ClickedFeature): { key: string; match: HighlightTarget } {
  const props = f.properties ?? {};
  const idKey = Object.keys(props).find((k) => ID_FIELD.test(k) && isScalar(props[k]));
  if (idKey) {
    return { key: `${f.layerId}|${idKey}=${String(props[idKey])}`,
             match: { source: f.source, sourceLayer: f.sourceLayer, props: { [idKey]: props[idKey] } } };
  }
  const key = `${f.layerId}|${JSON.stringify(props)}`;
  const measured = Object.values(props).some((v) => typeof v === "number" && !Number.isInteger(v));
  if (measured) return { key, match: { source: f.source, sourceLayer: f.sourceLayer, props } };
  return { key: `${key}|${turfBbox(f.geometry).join(",")}`, match: { geometry: f.geometry } };
}

// The features front first, each once: a polygon is drawn as a fill and an outline, so a click
// returns it twice. Then the plate's unit, when the click found one.
export function hitsOf(features: ClickedFeature[], unit: IdentifyResult | null, unitsSource: string, unitsLayer: string): PreviewHit[] {
  const seen = new Set<string>();
  const out: PreviewHit[] = [];
  for (const f of features) {
    const { key, match } = featureTarget(f);
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({ key, layerId: f.layerId, props: f.properties ?? {}, fid: f.id != null ? Number(f.id) : null,
               bbox: turfBbox(f.geometry) as FocusSel["bbox"], match });
  }
  if (unit?.kind === "unit") {
    // Every polygon of the unit lights, which shows where else on the map it is.
    const raw = unit.properties ?? {};
    const props = Object.fromEntries(["unit_code", "series_id"].filter((k) => isScalar(raw[k])).map((k) => [k, raw[k]]));
    out.push({ key: "unit", props: {}, fid: null, unit,
               bbox: unit.geometry ? turfBbox(unit.geometry) as FocusSel["bbox"] : undefined,
               match: Object.keys(props).length ? { source: unitsSource, sourceLayer: unitsLayer, props }
                 : unit.geometry ? { geometry: unit.geometry } : undefined });
  }
  return out;
}
