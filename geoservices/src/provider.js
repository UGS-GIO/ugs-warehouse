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

// Width plus height of a geometry's bounds, to rank features by how much of a tile they cover.
function extent(g) {
  let x0 = Infinity; let y0 = Infinity; let x1 = -Infinity; let y1 = -Infinity;
  const walk = (c) => {
    if (typeof c[0] === 'number') { if (c[0] < x0) x0 = c[0]; if (c[0] > x1) x1 = c[0]; if (c[1] < y0) y0 = c[1]; if (c[1] > y1) y1 = c[1]; } else c.forEach(walk);
  };
  walk(g.coordinates);
  return (x1 - x0) + (y1 - y0);
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
    // Small-scale requests on a heavy layer read its coarsest generalized copy that is still finer
    // than the request (vector/sink_archive.py).
    const overview = Object.entries(item.assets)
      .filter(([key, a]) => key.startsWith('overview') && Number(a['ugs:simplify_tolerance']) <= p.tolerance)
      .map(([, a]) => a)
      .sort((a, b) => b['ugs:simplify_tolerance'] - a['ugs:simplify_tolerance'])[0];
    const coarse = overview && p.kind === 'features' && p.geometry && !p.ids.length;
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
    // An overview's rows are small, so a drawing tile reads all of them rather than cutting the
    // Hilbert-ordered rows off partway across the tile.
    const readLimit = source === layer ? MAX_GEOMETRY_ROWS : Infinity;
    const rows = await parquet.select(source, thin ? { ...p, page: { ...p.page, limit: readLimit } } : p);
    const cap = p.page ? p.page.limit : p.geometry ? MAX_GEOMETRY_ROWS : Infinity;
    if (!thin && rows.length > cap) rows.length = cap + 1;
    const features = [];
    const cells = new Set();
    for (const r of await parquet.records(source, p, rows.slice(0, thin ? readLimit : cap + 1))) {
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
    // A drawing tile over the cap keeps its largest features, as the vector tiles drop the
    // smallest: flagging it exceeded makes Map Viewer split it into four and ask again, repeatedly.
    const tile = params.resultType === 'tile' && thin;
    if (tile && features.length > cap) {
      const size = (f) => (f.geometry.speck ? 0 : extent(f.geometry));
      features.sort((a, b) => size(b) - size(a));
    }
    const exceededTransferLimit = !tile && (features.length > cap || (thin && rows.length > readLimit));
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
