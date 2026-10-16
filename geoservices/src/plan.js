// GeoServices query params -> what to read from a layer's GeoParquet (src/parquet.js reads it).
//
// Ours: the columns a request needs, a bbox prefilter on the GeoParquet covering columns,
// objectIds, simplification, and paging when `where` is trivial. Koop applies everything else
// (exact geometry tests, `where`, statistics, projection, PBF) to what comes back.
'use strict';

const MERCATOR = new Set([102100, 102113, 3857, 900913]);
const R = 6378137;

const toLonLat = (x, y) => [
  (x / R) * (180 / Math.PI),
  (2 * Math.atan(Math.exp(y / R)) - Math.PI / 2) * (180 / Math.PI),
];
const toMercator = (lon, lat) => [
  (lon * Math.PI / 180) * R,
  Math.log(Math.tan(Math.PI / 4 + (Math.max(Math.min(lat, 85.0511), -85.0511) * Math.PI) / 360)) * R,
];

function parseJson(v) {
  if (typeof v !== 'string') return v;
  try { return JSON.parse(v); } catch { return v; }
}

function wkidOf(sr) {
  const v = parseJson(sr);
  if (typeof v === 'number') return v;
  if (typeof v === 'string' && /^\d+$/.test(v)) return Number(v);
  return v?.latestWkid ?? v?.wkid;
}

const isTrue = (v) => v === true || v === 'true';

// Any Esri geometry (or "xmin,ymin,xmax,ymax") -> [xmin, ymin, xmax, ymax] in its own SR.
function bboxOf(geometry) {
  const g = parseJson(geometry);
  if (typeof g === 'string') {
    const n = g.split(',').map(Number);
    return n.length === 4 && n.every(Number.isFinite) ? n : null;
  }
  if (!g || typeof g !== 'object') return null;
  if ('xmin' in g) return [g.xmin, g.ymin, g.xmax, g.ymax].map(Number);
  if ('x' in g) return [g.x, g.y, g.x, g.y].map(Number);
  const pts = [...(g.points ?? []), ...(g.paths ?? []).flat(), ...(g.rings ?? []).flat()];
  if (!pts.length) return null;
  const xs = pts.map((p) => p[0]);
  const ys = pts.map((p) => p[1]);
  return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
}

const METERS = { esriSRUnit_Meter: 1, esriSRUnit_Kilometer: 1000, esriSRUnit_Foot: 0.3048,
  esriSRUnit_StatuteMile: 1609.344, esriSRUnit_NauticalMile: 1852 };

// The request's spatial filter as a lon/lat box, grown by `distance` when one is given.
function filterBox(params) {
  if (params.geometry == null || params.geometry === '') return null;
  const box = bboxOf(params.geometry);
  if (!box || !box.every(Number.isFinite)) return null;
  const wkid = wkidOf(params.inSR) ?? wkidOf(parseJson(params.geometry)?.spatialReference) ?? 4326;
  let [xmin, ymin, xmax, ymax] = box;
  if (MERCATOR.has(wkid)) {
    [xmin, ymin] = toLonLat(xmin, ymin);
    [xmax, ymax] = toLonLat(xmax, ymax);
  } else if (wkid !== 4326 && wkid !== 4269) {
    return null;   // an SR we don't project: no prefilter, Koop still filters exactly
  }
  const meters = Number(params.distance || 0) * (METERS[params.units] ?? 1);
  const pad = meters / 111320;
  return [xmin - pad, ymin - pad, xmax + pad, ymax + pad];
}

const trivialWhere = (w) => w == null || /^\s*(1\s*=\s*1)?\s*$/i.test(String(w));

/**
 * @param params  raw GeoServices query params
 * @param layer   { columns: [{name, type}], point }
 * @returns {{ kind: 'count' | 'extent' | 'features', box, ids, columns, geometry, tolerance,
 *   page, filtersApplied, outSR }}
 */
function plan(params, layer) {
  const box = filterBox(params);
  const ids = String(params.objectIds ?? '').split(',').map((s) => s.trim()).filter(Boolean).map(Number);
  if (!ids.every(Number.isInteger)) throw Object.assign(new Error('objectIds must be integers'), { code: 400 });
  const simple = trivialWhere(params.where);
  const outSR = wkidOf(params.outSR);
  const base = { box, ids, outSR, filtersApplied: {} };

  // Counts and extents the file metadata answers without reading a row.
  if (simple && !box && !ids.length && isTrue(params.returnCountOnly) && !isTrue(params.returnExtentOnly)) {
    return { ...base, kind: 'count' };
  }
  if (simple && !box && !ids.length && isTrue(params.returnExtentOnly)) {
    return { ...base, kind: 'extent' };
  }

  // Columns: the requested outFields plus anything `where`, ordering or statistics read.
  const out = String(params.outFields ?? '*').split(',').map((f) => f.trim().toLowerCase());
  const mentions = [params.where, params.orderByFields, params.groupByFieldsForStatistics,
    typeof params.outStatistics === 'string' ? params.outStatistics : JSON.stringify(params.outStatistics ?? '')]
    .filter(Boolean).join(' ');
  const columns = layer.columns.filter((col) => col.name === 'feature_id' || out.includes('*')
    || out.includes(col.name.toLowerCase())
    || new RegExp(`\\b${col.name.replace(/[^\w]/g, '')}\\b`, 'i').test(mentions)).map((col) => col.name);

  // Ids and counts under a spatial filter still need geometry, for Koop's exact test.
  const geometry = isTrue(params.returnIdsOnly) || isTrue(params.returnCountOnly)
    ? Boolean(box)
    : !(params.returnGeometry === false || params.returnGeometry === 'false');
  const offset = Number(params.maxAllowableOffset);
  const tolerance = geometry && !layer.point && Number.isFinite(offset) && offset > 0
    ? (MERCATOR.has(outSR ?? 4326) ? offset / 111320 : offset)
    : 0;

  // Paging is ours only when Koop has nothing left to filter out. Rows are stored in feature_id
  // order, so a page of rows is a page of features.
  let page = null;
  const count = Number(params.resultRecordCount);
  const ordered = !params.orderByFields || /^\s*feature_id(\s+asc)?\s*$/i.test(String(params.orderByFields));
  if (simple && !box && ordered && !isTrue(params.returnCountOnly) && !params.outStatistics
    && Number.isInteger(count) && count > 0) {
    page = { offset: Math.max(0, Number(params.resultOffset || 0)), limit: count };
    Object.assign(base.filtersApplied, { offset: true, limit: true, resultRecordCount: true, orderByFields: true });
  }
  return { ...base, kind: 'features', columns, geometry, tolerance, page };
}

module.exports = { plan, filterBox, toMercator, wkidOf, MERCATOR };
