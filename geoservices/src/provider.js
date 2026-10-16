// Koop provider: `/ugs/rest/services/{topic}/{render}/FeatureServer` over the topic's GeoParquet.
'use strict';

const catalog = require('./catalog');
const parquet = require('./parquet');
const { plan, toMercator, MERCATOR } = require('./plan');
const { fromWkb } = require('./simplify');

const MAX_RECORDS = 2000;
// Map Viewer splits a tile whose response exceeds its limit into four and asks again, so a tile
// request gets ArcGIS Server's larger tile page; a dense small-scale tile then answers once.
const TILE_MAX_RECORDS = 8000;
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
    const maxRecords = params.resultType === 'tile' ? TILE_MAX_RECORDS : MAX_RECORDS;
    let p = plan(params, layer, maxRecords);
    // Small-scale requests on a heavy layer read its generalized copy (vector/sink_archive.py).
    const overview = item.assets.overview;
    const coarse = overview && p.kind === 'features' && p.geometry && !p.ids.length
      && p.tolerance >= Number(overview['ugs:simplify_tolerance'] ?? Infinity);
    const source = coarse ? await parquet.open(overview.href) : layer;
    if (coarse) p = plan(params, source, maxRecords);
    if (p.kind === 'count') return { type: 'FeatureCollection', features: [], count: layer.rows, metadata };
    if (p.kind === 'extent') {
      return { type: 'FeatureCollection', features: [], count: layer.rows, metadata,
        extent: layer.extent && projectExtent(layer.extent, p.outSR) };
    }

    // At a drawing scale, read past the page: specks are thinned below, as the vector tiles do,
    // so a dense small-scale tile still fits in one page instead of splitting into hundreds.
    const thin = p.page && p.tolerance > 0;
    const rows = await parquet.select(source, thin ? { ...p, page: { ...p.page, limit: MAX_GEOMETRY_ROWS } } : p);
    const cap = p.page ? p.page.limit : p.geometry ? MAX_GEOMETRY_ROWS : Infinity;
    if (!thin && rows.length > cap) rows.length = cap + 1;
    const features = [];
    const cells = new Set();
    for (const r of await parquet.records(source, p, rows.slice(0, thin ? MAX_GEOMETRY_ROWS : cap + 1))) {
      const properties = {};
      for (const name of p.columns) properties[name] = value(r[name]);
      const geometry = p.geometry ? fromWkb(r[source.geom], p.tolerance) : null;
      if (p.geometry && !geometry) continue;
      if (thin && geometry.speck) {
        // One speck per tolerance-sized cell per symbol class: the rest would draw on the same pixel.
        const [x, y] = geometry.type === 'Polygon' ? geometry.coordinates[0][0] : geometry.coordinates[0][0][0];
        const key = `${Math.floor(x / p.tolerance)}:${Math.floor(y / p.tolerance)}:${p.columns.filter((c) => c !== 'feature_id').map((c) => properties[c]).join('|')}`;
        if (cells.has(key)) continue;
        cells.add(key);
      }
      features.push({ type: 'Feature', properties, geometry });
    }
    const exceededTransferLimit = features.length > cap || (thin && rows.length > MAX_GEOMETRY_ROWS);
    if (features.length > cap) features.length = cap;
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
