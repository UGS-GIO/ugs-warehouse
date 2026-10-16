// Koop provider: `/ugs/rest/services/{topic}/{render}/FeatureServer` over the topic's GeoParquet.
'use strict';

const { DuckDBInstance } = require('@duckdb/node-api');
const catalog = require('./catalog');
const { plan, fieldType, toMercator, wkidOf, MERCATOR } = require('./plan');

// Our GeoParquet columns that are not attributes: geometry and its covering.
const INTERNAL = new Set(['bbox', 'bbox_xmin', 'bbox_ymin', 'bbox_xmax', 'bbox_ymax', 'target_epsg']);
const MAX_RECORDS = 2000;

let instance;
async function connect() {
  instance ??= DuckDBInstance.create(':memory:', {
    memory_limit: process.env.DUCKDB_MEMORY_LIMIT ?? '1GB',
    threads: process.env.DUCKDB_THREADS ?? '4',
  }).then(async (db) => {
    const c = await db.connect();
    // Extensions are installed in the image (Dockerfile); LOAD never downloads at request time.
    await c.run('LOAD spatial; LOAD httpfs;');
    return db;
  });
  return (await instance).connect();
}

// Schema, geometry type and extent per data file, cached like the catalog.
const layerInfo = (url) => catalog.cached(`layer:${url}`, async () => {
  const c = await connect();
  const described = (await c.runAndReadAll(`DESCRIBE SELECT * FROM read_parquet('${url.replace(/'/g, "''")}')`)).getRowObjectsJson();
  const all = described.map((r) => ({ name: String(r.column_name), type: String(r.column_type) }));
  const geomCol = all.find((col) => col.type.startsWith('GEOMETRY'));
  if (!geomCol) throw new Error(`${url}: no geometry column`);
  const bbox = all.some((col) => col.name === 'bbox_xmin') ? 'flat'
    : all.some((col) => col.name === 'bbox' && col.type.startsWith('STRUCT')) ? 'struct' : null;
  const q = `"${geomCol.name.replace(/"/g, '""')}"`;
  const src = `read_parquet('${url.replace(/'/g, "''")}')`;
  const [kind] = (await c.runAndReadAll(`SELECT ST_GeometryType(${q})::VARCHAR AS t FROM ${src} WHERE ${q} IS NOT NULL LIMIT 1`)).getRowObjectsJson();
  const ext = bbox === 'flat' ? 'min(bbox_xmin) AS xmin, min(bbox_ymin) AS ymin, max(bbox_xmax) AS xmax, max(bbox_ymax) AS ymax'
    : bbox === 'struct' ? 'min(bbox.xmin) AS xmin, min(bbox.ymin) AS ymin, max(bbox.xmax) AS xmax, max(bbox.ymax) AS ymax'
      : `ST_XMin(ST_Extent_Agg(${q})) AS xmin, ST_YMin(ST_Extent_Agg(${q})) AS ymin, ST_XMax(ST_Extent_Agg(${q})) AS xmax, ST_YMax(ST_Extent_Agg(${q})) AS ymax`;
  const [first] = (await c.runAndReadAll(`SELECT ${ext} FROM ${src}`)).getRowObjectsJson();
  const type = String(kind?.t ?? '').toUpperCase();
  const geometryType = type.includes('POINT') ? 'Point' : type.includes('LINE') ? 'LineString' : 'Polygon';
  const columns = all.filter((col) => col !== geomCol && !INTERNAL.has(col.name));
  return {
    url, geom: geomCol.name, bbox, columns, geometryType, point: geometryType === 'Point',
    extent: first ? { xmin: first.xmin, ymin: first.ymin, xmax: first.xmax, ymax: first.ymax, spatialReference: { wkid: 4326 } } : undefined,
    fields: columns.map((col) => ({ name: col.name, type: fieldType(col), alias: col.name })),
  };
});

function projectExtent(e, wkid) {
  if (!MERCATOR.has(wkid)) return { ...e, spatialReference: { wkid: 4326 } };
  const [xmin, ymin] = toMercator(e.xmin, e.ymin);
  const [xmax, ymax] = toMercator(e.xmax, e.ymax);
  return { xmin, ymin, xmax, ymax, spatialReference: { wkid: 102100, latestWkid: 3857 } };
}

class Model {
  async getData(req, callback) {
    try {
      callback(null, await this.load(req));
    } catch (err) {
      callback(Object.assign(err, { code: Number.isInteger(err.code) ? err.code : 500 }));
    }
  }

  async load(req) {
    const { host: topicId, id: renderName } = req.params;
    const item = await catalog.topic(topicId);
    const [layer, style] = await Promise.all([layerInfo(item.assets.data.href), catalog.render(item, renderName)]);
    const metadata = {
      name: `${item.properties?.title ?? topicId}${renderName === 'default' ? '' : ` (${renderName})`}`,
      description: item.properties?.description ?? '',
      idField: 'feature_id',
      geometryType: layer.geometryType,
      fields: layer.fields,
      extent: layer.extent,
      maxRecordCount: MAX_RECORDS,
      ...(style.renderer ? { renderer: style.renderer } : {}),
      ...(style.labelingInfo ? { labelingInfo: style.labelingInfo } : {}),
      ...(style.minScale ? { minScale: style.minScale } : {}),
    };
    // Service and layer descriptors only need the metadata.
    if (!/\/query\/?$/.test(req.path)) return { type: 'FeatureCollection', features: [], metadata };

    const params = { ...req.query, ...req.body };
    const p = plan(params, layer);
    const c = await connect();
    const rows = (await c.runAndReadAll(p.sql)).getRowObjectsJson();
    if (p.kind === 'count') return { type: 'FeatureCollection', features: [], count: rows[0]?.n ?? 0, metadata };
    if (p.kind === 'extent') {
      const [{ n = 0, ...e } = {}] = rows;
      return { type: 'FeatureCollection', features: [], count: n, metadata,
        extent: n ? projectExtent(e, wkidOf(params.outSR)) : undefined };
    }
    let exceededTransferLimit = false;
    if (p.limit !== undefined && rows.length > p.limit) {
      rows.length = p.limit;
      exceededTransferLimit = true;
    }
    const features = [];
    for (const { __geom, ...properties } of rows) {
      const geometry = __geom ? JSON.parse(__geom) : null;
      // A feature simplified to nothing at this scale is too small to draw; leave it out.
      if (geometry && !geometry.coordinates?.length) continue;
      features.push({ type: 'Feature', properties, geometry });
    }
    return { type: 'FeatureCollection', features, metadata: { ...metadata, exceededTransferLimit }, filtersApplied: p.filtersApplied };
  }
}

module.exports = {
  name: 'ugs',
  type: 'provider',
  version: require('../package.json').version,
  hosts: true,            // {topic}/{render}: one FeatureServer per published symbology
  disableIdParam: false,
  Model,
};
