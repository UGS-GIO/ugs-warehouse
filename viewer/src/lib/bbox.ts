// Bounding-box tests in lon/lat, [west, south, east, north]. Here rather than maplibre's
// LngLatBounds because the export and offline-table code use them on pages that load no map.

export type Bbox = readonly [number, number, number, number];

/** Whether two boxes share any area (touching edges count). */
export const overlaps = (a: Bbox, b: Bbox): boolean =>
  a[0] <= b[2] && a[2] >= b[0] && a[1] <= b[3] && a[3] >= b[1];

/** Whether a point lies in a box (on the edge counts). */
export const contains = (b: Bbox, lon: number, lat: number): boolean =>
  b[0] <= lon && lon <= b[2] && b[1] <= lat && lat <= b[3];

/** The first four numbers of `v` as a box (a URL param, a STAC bbox), or undefined. */
export function toBbox(v: unknown): [number, number, number, number] | undefined {
  if (!Array.isArray(v) || v.length < 4) return undefined;
  const [w, s, e, n] = v;
  return [w, s, e, n].every(Number.isFinite) ? [w, s, e, n] : undefined;
}

/** A [w,s,e,n] box as a closed rectangle ring (GeoJSON Polygon coordinates). */
export const bboxRing = (b: readonly number[]): GeoJSON.Position[][] =>
  [[[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]], [b[0], b[1]]]];
