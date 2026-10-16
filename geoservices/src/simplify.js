// WKB -> GeoJSON for drawing at a scale: 2D, Douglas-Peucker simplified to `tolerance` (degrees),
// in one pass over the bytes so only the kept points are ever allocated.
// A polygon or line smaller than the tolerance keeps a speck (its bounding box, or its two ends)
// rather than vanishing, so a statewide view still shows where many small features are, as the
// vector tiles do. Holes that simplify away are dropped.
'use strict';

// Indices to keep of `n` points at `xs`/`ys`, by Douglas-Peucker.
function keep(xs, ys, n, tolerance) {
  const kept = new Uint8Array(n);
  kept[0] = kept[n - 1] = 1;
  if (n <= 2 || tolerance <= 0) return kept.fill(1);
  const t2 = tolerance * tolerance;
  const stack = [0, n - 1];
  while (stack.length) {
    const b = stack.pop();
    const a = stack.pop();
    const dx = xs[b] - xs[a];
    const dy = ys[b] - ys[a];
    const len2 = dx * dx + dy * dy;
    let worst = -1;
    let index = -1;
    for (let i = a + 1; i < b; i++) {
      let t = len2 ? ((xs[i] - xs[a]) * dx + (ys[i] - ys[a]) * dy) / len2 : 0;
      t = t < 0 ? 0 : t > 1 ? 1 : t;
      const ex = xs[i] - (xs[a] + t * dx);
      const ey = ys[i] - (ys[a] + t * dy);
      const d2 = ex * ex + ey * ey;
      if (d2 > worst) { worst = d2; index = i; }
    }
    if (worst > t2) {
      kept[index] = 1;
      stack.push(a, index, index, b);
    }
  }
  return kept;
}

class Reader {
  constructor(bytes) {
    this.view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    this.offset = 0;
    this.little = true;
  }

  header() {
    this.little = this.view.getUint8(this.offset) === 1;
    let type = this.view.getUint32(this.offset + 1, this.little);
    this.offset += 5;
    // EWKB flags, then ISO 1000s for Z/M
    let dims = 2;
    if (type & 0x80000000) dims++;
    if (type & 0x40000000) dims++;
    if (type & 0x20000000) this.offset += 4;   // SRID
    type &= 0x0fffffff;
    if (type > 1000) { dims += type >= 3000 ? 2 : 1; type %= 1000; }
    return { type, dims };
  }

  uint() { const v = this.view.getUint32(this.offset, this.little); this.offset += 4; return v; }

  // One run of `n` points, simplified; `minPoints` below which it is a speck.
  run(dims, tolerance) {
    const n = this.uint();
    const xs = new Float64Array(n);
    const ys = new Float64Array(n);
    for (let i = 0; i < n; i++) {
      xs[i] = this.view.getFloat64(this.offset, this.little);
      ys[i] = this.view.getFloat64(this.offset + 8, this.little);
      this.offset += 8 * dims;
    }
    const kept = keep(xs, ys, n, tolerance);
    const out = [];
    for (let i = 0; i < n; i++) if (kept[i]) out.push([xs[i], ys[i]]);
    return { out, xs, ys, n };
  }

  point(dims) {
    const x = this.view.getFloat64(this.offset, this.little);
    const y = this.view.getFloat64(this.offset + 8, this.little);
    this.offset += 8 * dims;
    return [x, y];
  }
}

function speck(xs, ys, n) {
  let x0 = Infinity; let y0 = Infinity; let x1 = -Infinity; let y1 = -Infinity;
  for (let i = 0; i < n; i++) {
    if (xs[i] < x0) x0 = xs[i]; if (xs[i] > x1) x1 = xs[i];
    if (ys[i] < y0) y0 = ys[i]; if (ys[i] > y1) y1 = ys[i];
  }
  return [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]];
}

function line(r, dims, t) {
  const { out, xs, ys, n } = r.run(dims, t);
  return out.length >= 2 ? out : [[xs[0], ys[0]], [xs[n - 1], ys[n - 1]]];
}

function polygon(r, dims, t, info) {
  const rings = [];
  const count = r.uint();
  for (let i = 0; i < count; i++) {
    const { out, xs, ys, n } = r.run(dims, t);
    if (out.length >= 4) { rings.push(out); info.whole = true; } else if (i === 0) rings.push(speck(xs, ys, n));
  }
  return rings;
}

function geometry(r, t, info) {
  const { type, dims } = r.header();
  const many = (one) => { const n = r.uint(); const out = []; for (let i = 0; i < n; i++) { r.header(); out.push(one()); } return out; };
  switch (type) {
    case 1: return { type: 'Point', coordinates: r.point(dims) };
    case 2: return { type: 'LineString', coordinates: line(r, dims, t) };
    case 3: return { type: 'Polygon', coordinates: polygon(r, dims, t, info) };
    case 4: return { type: 'MultiPoint', coordinates: many(() => r.point(dims)) };
    case 5: return { type: 'MultiLineString', coordinates: many(() => line(r, dims, t)) };
    case 6: return { type: 'MultiPolygon', coordinates: many(() => polygon(r, dims, t, info)) };
    default: throw new Error(`unsupported WKB geometry type ${type}`);
  }
}

/**
 * GeoJSON geometry from WKB bytes, simplified to `tolerance`; null for no geometry. `speck` is
 * set when a polygon kept no ring at that tolerance, so the caller can thin overlapping specks.
 */
function fromWkb(bytes, tolerance = 0) {
  if (!bytes || !bytes.byteLength) return null;
  const info = { whole: false };
  const g = geometry(new Reader(bytes), tolerance, info);
  if (/Polygon/.test(g.type) && !info.whole) Object.defineProperty(g, 'speck', { value: true });
  return g;
}

module.exports = { fromWkb, keep };
