// Koop provider: `/ugs/rest/services/{topic}/{render}/FeatureServer` over the topic's GeoParquet.
'use strict';

const catalog = require('./catalog');
const parquet = require('./parquet');
const { plan, toMercator, MERCATOR } = require('./plan');
const { simplify } = require('./simplify');

const MAX_RECORDS = 2000;
// A request Koop must still filter by `where` reads at most this many geometries, so one query
// cannot exhaust memory; past it the response says the transfer limit was exceeded.
const MAX_GEOMETRY_ROWS = Number(process.env.MAX_GEOMETRY_ROWS ?? 50000);

function projectExtent(e, wkid) {
  if (!MERCATOR.has(wkid)) return { ...e, spatialReference: { wkid: 4326 } };
  const [xmin, ymin] = toMercator(e.xmin, e.ymin);
  const [xmax, ymax] = toMercator(e.xmax, e.ymax);
  return { xmin, ymin, xmax, ymax, spatialReference: { wkid: 102100, latestWkid: 3857 } };
}

// Parquet values as the JSON types the layer's fields declare: BigInt -> number, Date -> epoch ms.
const value = (v) => (typeof v === 'bigint' ? Number(v) : v instanceof Date ? v.getTime() : v);

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
    const [layer, style] = await Promise.all([parquet.open(item.assets.data.href), catalog.render(item, renderName)]);
    const metadata = {
      name: `${item.properties?.title ?? topicId}${renderName === 'default' ? '' : ` (${renderName})`}`,
      description: item.properties?.description ?? '',
      idField: 'feature_id',
      geometryType: layer.geometryType,
      fields: layer.columns.map((col) => ({ name: col.name, type: col.type, alias: col.name })),
      ...(layer.extent ? { extent: { ...layer.extent, spatialReference: { wkid: 4326 } } } : {}),
      maxRecordCount: MAX_RECORDS,
      ...(style.renderer ? { renderer: style.renderer } : {}),
      ...(style.labelingInfo ? { labelingInfo: style.labelingInfo } : {}),
      ...(style.minScale ? { minScale: style.minScale } : {}),
    };
    // Service and layer descriptors only need the metadata.
    if (!/\/query\/?$/.test(req.path)) return { type: 'FeatureCollection', features: [], metadata };

    const params = { ...req.query, ...req.body };
    let p = plan(params, layer, MAX_RECORDS);
    // Small-scale requests on a heavy layer read its generalized copy (vector/sink_archive.py).
    const overview = item.assets.overview;
    const coarse = overview && p.kind === 'features' && p.geometry && !p.ids.length
      && p.tolerance >= Number(overview['ugs:simplify_tolerance'] ?? Infinity);
    const source = coarse ? await parquet.open(overview.href) : layer;
    if (coarse) p = plan(params, source, MAX_RECORDS);
    if (p.kind === 'count') return { type: 'FeatureCollection', features: [], count: layer.rows, metadata };
    if (p.kind === 'extent') {
      return { type: 'FeatureCollection', features: [], count: layer.rows, metadata,
        extent: layer.extent && projectExtent(layer.extent, p.outSR) };
    }

    const rows = await parquet.select(source, p);
    const cap = p.page ? p.page.limit : p.geometry ? MAX_GEOMETRY_ROWS : Infinity;
    const exceededTransferLimit = rows.length > cap;
    if (exceededTransferLimit) rows.length = cap;
    const features = [];
    for (const r of await parquet.records(source, p, rows)) {
      const properties = {};
      for (const name of p.columns) properties[name] = value(r[name]);
      const geometry = p.geometry ? simplify(r[source.geom], p.tolerance) : null;
      // A feature simplified to nothing at this scale is too small to draw; leave it out.
      if (p.geometry && !geometry) continue;
      features.push({ type: 'Feature', properties, geometry });
    }
    return { type: 'FeatureCollection', features, metadata: { ...metadata, exceededTransferLimit },
      filtersApplied: p.filtersApplied };
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
