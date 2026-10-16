// Read a layer's GeoParquet over https, fetching only what a request needs.
//
// Row groups are pruned on the bbox covering columns' statistics; inside a row group, the small
// bbox columns pick the matching rows and the page index lets hyparquet fetch only the pages
// holding them (the warehouse writes ~100-row pages; vector/sink_archive.py).
'use strict';

const zlib = require('node:zlib');
const catalog = require('./catalog');

let lib;
// Node's native zstd is several times faster than the JS one, and geometry stays WKB bytes: the
// simplifier reads it directly (src/simplify.js), with no GeoJSON built for points it drops.
const hyparquet = () => (lib ??= Promise.all([import('hyparquet'), import('hyparquet-compressors')])
  .then(([h, c]) => ({ ...h, compressors: zlib.zstdDecompressSync
    ? { ...c.compressors, ZSTD: (input) => new Uint8Array(zlib.zstdDecompressSync(input)) }
    : c.compressors })));
const WKB = { geometryFromBytes: (bytes) => bytes };

// Our GeoParquet columns that are not attributes: the covering and the source CRS.
const INTERNAL = new Set(['bbox', 'bbox_xmin', 'bbox_ymin', 'bbox_xmax', 'bbox_ymax', 'target_epsg']);
const BBOX = ['bbox_xmin', 'bbox_ymin', 'bbox_xmax', 'bbox_ymax'];
const GAP = 64;   // join row ranges this close; fewer requests beat a few skipped rows
const CACHE_BYTES = Number(process.env.PAGE_CACHE_MB ?? 512) * 1024 * 1024;

// Byte ranges already fetched, shared by every layer, least recently used out first.
const pages = new Map();
let cachedBytes = 0;
function cachedFile(file, url) {
  return {
    byteLength: file.byteLength,
    slice(start, end) {
      const key = `${url}:${start}:${end}`;
      const hit = pages.get(key);
      if (hit) { pages.delete(key); pages.set(key, hit); return hit; }
      const p = Promise.resolve(file.slice(start, end)).then((buf) => {
        cachedBytes += buf.byteLength;
        for (const [k, v] of pages) {
          if (cachedBytes <= CACHE_BYTES) break;
          if (v instanceof Promise) continue;
          cachedBytes -= v.byteLength;
          pages.delete(k);
        }
        pages.set(key, buf);
        return buf;
      }, (err) => { pages.delete(key); throw err; });
      pages.set(key, p);
      return p;
    },
  };
}

const field = (rg, name, stat) => {
  const col = rg.columns.find((c) => c.meta_data?.path_in_schema.join('.') === name);
  const v = col?.meta_data?.statistics?.[stat];
  return v == null ? undefined : Number(v);
};

function fieldType(el) {
  const logical = el.logical_type?.type;
  if (el.name === 'feature_id') return 'Integer';
  if (logical === 'TIMESTAMP' || logical === 'DATE' || /TIMESTAMP|DATE/.test(el.converted_type ?? '')) return 'Date';
  if (el.type === 'INT32' || el.type === 'BOOLEAN') return 'Integer';
  if (el.type === 'INT64' || el.type === 'DOUBLE' || el.type === 'FLOAT') return 'Double';
  return 'String';
}

// Schema, geometry type, extent and row-group bounds per data file, cached like the catalog.
const open = (url) => catalog.cached(`layer:${url}`, async () => {
  const h = await hyparquet();
  const raw = /^https?:/.test(url) ? await h.asyncBufferFromUrl({ url }) : await h.asyncBufferFromFile(url);
  const file = cachedFile(raw, url);
  const metadata = await h.parquetMetadataAsync(file);
  const geo = JSON.parse(metadata.key_value_metadata?.find((kv) => kv.key === 'geo')?.value ?? '{}');
  const geom = geo.primary_column ?? 'geom';
  const types = geo.columns?.[geom]?.geometry_types ?? [];
  const top = h.parquetSchema(metadata).children;
  const names = new Set(top.map((c) => c.element.name));
  const bbox = BBOX.every((n) => names.has(n));
  const columns = top.map((c) => c.element)
    .filter((el) => el.name !== geom && !INTERNAL.has(el.name) && !el.num_children)
    .map((el) => ({ name: el.name, type: fieldType(el) }));

  let start = 0;
  const groups = metadata.row_groups.map((rg) => {
    const rows = Number(rg.num_rows);
    const g = { start, rows, xmin: field(rg, 'bbox_xmin', 'min_value'), ymin: field(rg, 'bbox_ymin', 'min_value'),
      xmax: field(rg, 'bbox_xmax', 'max_value'), ymax: field(rg, 'bbox_ymax', 'max_value'),
      idMin: field(rg, 'feature_id', 'min_value'), idMax: field(rg, 'feature_id', 'max_value') };
    start += rows;
    return g;
  });
  const extent = bbox ? {
    xmin: Math.min(...groups.map((g) => g.xmin)), ymin: Math.min(...groups.map((g) => g.ymin)),
    xmax: Math.max(...groups.map((g) => g.xmax)), ymax: Math.max(...groups.map((g) => g.ymax)),
  } : undefined;
  const type = String(types[0] ?? '').replace(/ Z$/, '');
  const geometryType = /Point/.test(type) ? 'Point' : /LineString/.test(type) ? 'LineString' : 'Polygon';
  // feature_id is the row number + 1 in every archive we write; trust it only where the stats say so.
  const idIsRow = groups.every((g) => g.idMin === g.start + 1 && g.idMax === g.start + g.rows);
  const geomChunk = metadata.row_groups[0]?.columns.find((c) => c.meta_data?.path_in_schema.join('.') === geom);
  return { url, file, metadata, geom, bbox, columns, groups, extent, geometryType, point: geometryType === 'Point',
    rows: start, idIsRow, paged: Boolean(geomChunk?.offset_index_offset) };
});

// [start, end) row ranges from sorted row numbers, joining near neighbours.
function ranges(rows) {
  const out = [];
  for (const r of rows) {
    const last = out[out.length - 1];
    if (last && r - last[1] <= GAP) last[1] = r + 1;
    else out.push([r, r + 1]);
  }
  return out;
}

const overlaps = (g, [x0, y0, x1, y1]) =>
  !(g.xmax < x0 || g.xmin > x1 || g.ymax < y0 || g.ymin > y1);

async function read(h, layer, columns, rowStart, rowEnd) {
  return h.parquetReadObjects({ file: layer.file, metadata: layer.metadata, columns, rowStart, rowEnd,
    compressors: h.compressors, parsers: WKB, useOffsetIndex: true });
}

// The rows a plan selects, in file (= feature_id) order.
async function select(layer, p) {
  const h = await hyparquet();
  let rows;
  if (p.ids.length) {
    if (layer.idIsRow) {
      rows = [...new Set(p.ids)].map((id) => id - 1).filter((r) => r >= 0 && r < layer.rows);
    } else {
      const ids = new Set(p.ids);
      const all = await read(h, layer, ['feature_id'], 0, layer.rows);
      rows = all.flatMap((r, i) => (ids.has(Number(r.feature_id)) ? [i] : []));
    }
  } else if (p.box && layer.bbox) {
    rows = [];
    const [x0, y0, x1, y1] = p.box;
    for (const g of layer.groups.filter((gr) => overlaps(gr, p.box))) {
      const b = await read(h, layer, ['bbox_xmin', 'bbox_ymin', 'bbox_xmax', 'bbox_ymax'], g.start, g.start + g.rows);
      b.forEach((r, i) => {
        if (r.bbox_xmax >= x0 && r.bbox_xmin <= x1 && r.bbox_ymax >= y0 && r.bbox_ymin <= y1) rows.push(g.start + i);
      });
    }
  } else if (p.page) {
    const end = Math.min(layer.rows, p.page.offset + p.page.limit + 1);   // one extra: is there more?
    rows = Array.from({ length: Math.max(0, end - p.page.offset) }, (_, i) => p.page.offset + i);
  } else {
    rows = Array.from({ length: layer.rows }, (_, i) => i);
  }
  if (p.box && p.ids.length && layer.bbox) {
    // both filters: keep the id rows the box prefilter keeps
    const inBox = new Set(await select(layer, { ...p, ids: [], page: null }));
    rows = rows.filter((r) => inBox.has(r));
  }
  rows.sort((a, b) => a - b);
  // A page of a filtered selection (the plain page above is already one); one extra row says more.
  return p.page && (p.box || p.ids.length) ? rows.slice(p.page.offset, p.page.offset + p.page.limit + 1) : rows;
}

// Records for the selected rows: the plan's columns, plus geometry as GeoJSON when asked.
async function records(layer, p, rows) {
  const h = await hyparquet();
  const columns = [...p.columns, ...(p.geometry ? [layer.geom] : [])];
  const want = new Set(rows);
  const out = [];
  // Without a page index every range decodes its whole column chunk, so read one span instead.
  const spans = layer.paged || !rows.length ? ranges(rows) : [[rows[0], rows[rows.length - 1] + 1]];
  for (const [a, z] of spans) {
    const got = await read(h, layer, columns, a, z);
    got.forEach((r, i) => { if (want.has(a + i)) out.push(r); });
  }
  return out;
}

module.exports = { open, select, records };
