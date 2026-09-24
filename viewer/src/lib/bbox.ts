// Bounding-box tests in lon/lat, [west, south, east, north]. Here rather than maplibre's
// LngLatBounds because the export and offline-table code use them on pages that load no map.

export type Bbox = readonly [number, number, number, number];

/** Whether two boxes share any area (touching edges count). */
export const overlaps = (a: Bbox, b: Bbox): boolean =>
  a[0] <= b[2] && a[2] >= b[0] && a[1] <= b[3] && a[3] >= b[1];

/** Whether a point lies in a box (on the edge counts). */
export const contains = (b: Bbox, lon: number, lat: number): boolean =>
  b[0] <= lon && lon <= b[2] && b[1] <= lat && lat <= b[3];
