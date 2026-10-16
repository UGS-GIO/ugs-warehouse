// GeoJSON geometry for drawing at a scale: 2D, Douglas-Peucker simplified to `tolerance` (degrees).
// A ring or line that simplifies away is dropped; a feature with nothing left returns null.
'use strict';

function douglasPeucker(points, tolerance) {
  if (points.length <= 2 || tolerance <= 0) return points;
  const keep = new Uint8Array(points.length);
  keep[0] = keep[points.length - 1] = 1;
  const stack = [[0, points.length - 1]];
  const t2 = tolerance * tolerance;
  while (stack.length) {
    const [a, b] = stack.pop();
    const [ax, ay] = points[a];
    const [bx, by] = points[b];
    const dx = bx - ax;
    const dy = by - ay;
    const len2 = dx * dx + dy * dy;
    let worst = -1;
    let index = -1;
    for (let i = a + 1; i < b; i++) {
      const [px, py] = points[i];
      let t = len2 ? ((px - ax) * dx + (py - ay) * dy) / len2 : 0;
      t = Math.max(0, Math.min(1, t));
      const ex = px - (ax + t * dx);
      const ey = py - (ay + t * dy);
      const d2 = ex * ex + ey * ey;
      if (d2 > worst) { worst = d2; index = i; }
    }
    if (worst > t2) {
      keep[index] = 1;
      stack.push([a, index], [index, b]);
    }
  }
  return points.filter((_, i) => keep[i]);
}

const flat = (coords) => coords.map(([x, y]) => [x, y]);
const line = (coords, t) => {
  const out = douglasPeucker(flat(coords), t);
  return out.length >= 2 ? out : null;
};
const ring = (coords, t) => {
  const out = douglasPeucker(flat(coords), t);
  return out.length >= 4 ? out : null;
};
const polygon = (rings, t) => {
  const [outer, ...holes] = rings.map((r) => ring(r, t));
  return outer ? [outer, ...holes.filter(Boolean)] : null;
};

function simplify(geometry, tolerance) {
  if (!geometry) return null;
  const t = tolerance;
  switch (geometry.type) {
    case 'Point': return { type: 'Point', coordinates: geometry.coordinates.slice(0, 2) };
    case 'MultiPoint': return { type: 'MultiPoint', coordinates: flat(geometry.coordinates) };
    case 'LineString': {
      const c = line(geometry.coordinates, t);
      return c && { type: 'LineString', coordinates: c };
    }
    case 'MultiLineString': {
      const c = geometry.coordinates.map((l) => line(l, t)).filter(Boolean);
      return c.length ? { type: 'MultiLineString', coordinates: c } : null;
    }
    case 'Polygon': {
      const c = polygon(geometry.coordinates, t);
      return c && { type: 'Polygon', coordinates: c };
    }
    case 'MultiPolygon': {
      const c = geometry.coordinates.map((p) => polygon(p, t)).filter(Boolean);
      return c.length ? { type: 'MultiPolygon', coordinates: c } : null;
    }
    default: return geometry;
  }
}

module.exports = { simplify, douglasPeucker };
