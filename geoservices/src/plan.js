// GeoServices query params -> one DuckDB statement over a layer's GeoParquet.
//
// Pushed down: the columns a request needs, a bbox prefilter on the GeoParquet covering columns,
// objectIds, simplification, and paging when `where` is trivial. Koop applies everything else
// (exact geometry tests, `where`, statistics, projection, PBF) to what comes back.
//
// `where` is never sent to DuckDB. It is client SQL, and DuckDB can read files and URLs, so
// passing it through would let a request read anything the service can. Koop evaluates it in
// memory instead; until then this only prunes by bbox.
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

const quote = (name) => `"${name.replace(/"/g, '""')}"`;

// A column as a JSON-safe value of the Esri type the layer reports (see fieldType).
function selectExpr(col) {
  const q = quote(col.name);
  if (col.name === 'feature_id') return `${q}::INTEGER AS ${q}`;
  if (/^(TIMESTAMP|DATE)/.test(col.type)) return `epoch_ms(${q}::TIMESTAMP)::DOUBLE AS ${q}`;
  if (/^(BIGINT|HUGEINT|UBIGINT|UINTEGER|DECIMAL|DOUBLE|FLOAT|REAL)/.test(col.type)) return `${q}::DOUBLE AS ${q}`;
  if (/^(INTEGER|SMALLINT|TINYINT|USMALLINT|UTINYINT)/.test(col.type)) return `${q}::INTEGER AS ${q}`;
  if (col.type === 'BOOLEAN') return `${q}::INTEGER AS ${q}`;
  if (col.type === 'VARCHAR') return q;
  return `to_json(${q})::VARCHAR AS ${q}`;   // lists and structs, as text
}

function fieldType(col) {
  if (col.name === 'feature_id') return 'Integer';
  if (/^(TIMESTAMP|DATE)/.test(col.type)) return 'Date';
  if (/^(BIGINT|HUGEINT|UBIGINT|UINTEGER|DECIMAL|DOUBLE|FLOAT|REAL)/.test(col.type)) return 'Double';
  if (/^(INTEGER|SMALLINT|TINYINT|USMALLINT|UTINYINT|BOOLEAN)/.test(col.type)) return 'Integer';
  return 'String';
}

const sqlString = (s) => `'${String(s).replace(/'/g, "''")}'`;
const trivialWhere = (w) => w == null || /^\s*(1\s*=\s*1)?\s*$/i.test(String(w));

/**
 * @param params  raw GeoServices query params
 * @param layer   { url, geom, columns: [{name, type}], bbox: 'flat' | 'struct' | null, point }
 * @returns {{ kind: 'count' | 'extent' | 'features', sql: string, filtersApplied: object, limit?: number }}
 */
function plan(params, layer) {
  const byName = new Map(layer.columns.map((c) => [c.name.toLowerCase(), c]));
  const conditions = [];
  const box = filterBox(params);
  if (box) {
    const [xmin, ymin, xmax, ymax] = box;
    const b = layer.bbox === 'struct'
      ? { x0: 'bbox.xmin', y0: 'bbox.ymin', x1: 'bbox.xmax', y1: 'bbox.ymax' }
      : { x0: 'bbox_xmin', y0: 'bbox_ymin', x1: 'bbox_xmax', y1: 'bbox_ymax' };
    conditions.push(layer.bbox
      ? `${b.x1} >= ${xmin} AND ${b.x0} <= ${xmax} AND ${b.y1} >= ${ymin} AND ${b.y0} <= ${ymax}`
      : `ST_Intersects(${quote(layer.geom)}, ST_MakeEnvelope(${xmin}, ${ymin}, ${xmax}, ${ymax}))`);
  }
  const ids = String(params.objectIds ?? '').split(',').map((s) => s.trim()).filter(Boolean).map(Number);
  if (ids.length) {
    if (!ids.every(Number.isInteger)) throw new Error('objectIds must be integers');
    conditions.push(`feature_id IN (${ids.join(', ')})`);
  }
  const from = `read_parquet(${sqlString(layer.url)})`;
  const whereSql = conditions.length ? ` WHERE ${conditions.join(' AND ')}` : '';
  const simple = trivialWhere(params.where);

  // Counts and extents the covering columns answer without reading geometry.
  if (simple && !box && !ids.length && isTrue(params.returnCountOnly) && !isTrue(params.returnExtentOnly)) {
    return { kind: 'count', sql: `SELECT count(*)::INTEGER AS n FROM ${from}`, filtersApplied: {} };
  }
  if (simple && !box && !ids.length && isTrue(params.returnExtentOnly) && layer.bbox) {
    const p = layer.bbox === 'struct' ? 'bbox.' : 'bbox_';
    return { kind: 'extent', filtersApplied: {}, outSR: wkidOf(params.outSR),
      sql: `SELECT min(${p}xmin) AS xmin, min(${p}ymin) AS ymin, max(${p}xmax) AS xmax, max(${p}ymax) AS ymax, count(*)::INTEGER AS n FROM ${from}` };
  }

  // Columns: the requested outFields plus anything `where`, ordering or statistics read.
  const wanted = new Set(['feature_id']);
  const out = String(params.outFields ?? '*');
  const all = out.split(',').some((f) => f.trim() === '*');
  const mentions = [params.where, params.orderByFields, params.groupByFieldsForStatistics,
    typeof params.outStatistics === 'string' ? params.outStatistics : JSON.stringify(params.outStatistics ?? '')]
    .filter(Boolean).join(' ');
  for (const col of layer.columns) {
    if (all || out.split(',').map((f) => f.trim().toLowerCase()).includes(col.name.toLowerCase())
      || new RegExp(`\\b${col.name.replace(/[^\w]/g, '')}\\b`, 'i').test(mentions)) wanted.add(col.name);
  }
  const cols = [...wanted].map((n) => byName.get(n.toLowerCase())).filter(Boolean);
  const select = cols.map(selectExpr);

  const needGeometry = !isTrue(params.returnIdsOnly) && !isTrue(params.returnCountOnly)
    ? !(params.returnGeometry === false || params.returnGeometry === 'false')
    : Boolean(box);   // ids/counts under a spatial filter still need geometry for the exact test
  if (needGeometry || isTrue(params.returnExtentOnly)) {
    let g = `ST_Force2D(${quote(layer.geom)})`;   // our Z is always 0; dropping it shrinks every response
    const offset = Number(params.maxAllowableOffset);
    if (!layer.point && Number.isFinite(offset) && offset > 0) {
      const tolerance = MERCATOR.has(wkidOf(params.outSR) ?? 4326) ? offset / 111320 : offset;
      // Plain Douglas-Peucker: 5x faster than the topology-preserving form on detailed polygons,
      // and a drawing at this scale can't show the difference.
      g = `ST_Simplify(${g}, ${tolerance})`;
    }
    select.push(`ST_AsGeoJSON(${g}) AS __geom`);
  }

  // Paging is ours only when Koop has nothing left to filter out.
  const filtersApplied = {};
  let tail = '';
  let limit;
  if (simple && !isTrue(params.returnCountOnly) && !isTrue(params.returnExtentOnly) && !params.outStatistics) {
    const order = String(params.orderByFields ?? '').split(',').map((s) => s.trim()).filter(Boolean)
      .map((s) => {
        const [name, dir] = s.split(/\s+/);
        const col = byName.get(name.toLowerCase());
        return col ? `${quote(col.name)} ${/^desc$/i.test(dir ?? '') ? 'DESC' : 'ASC'}` : null;
      });
    const orderSql = order.length && order.every(Boolean) ? order.join(', ') : 'feature_id';
    const count = Number(params.resultRecordCount);
    const offset = Number(params.resultOffset || 0);
    if (Number.isInteger(count) && count > 0 && !box) {
      limit = count;
      tail = ` ORDER BY ${orderSql} LIMIT ${count + 1} OFFSET ${Math.max(0, offset)}`;
      Object.assign(filtersApplied, { offset: true, limit: true, resultRecordCount: true, orderByFields: true });
    } else {
      tail = ` ORDER BY ${orderSql}`;
    }
  }
  return { kind: 'features', sql: `SELECT ${select.join(', ')} FROM ${from}${whereSql}${tail}`, filtersApplied, limit };
}

module.exports = { plan, fieldType, filterBox, toMercator, wkidOf, MERCATOR };
