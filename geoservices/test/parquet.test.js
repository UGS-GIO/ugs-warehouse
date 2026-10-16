'use strict';

// fixtures/grid.parquet: 1,200 small squares on a 40-wide grid from (-112, 40), 0.05 deg apart,
// written by the warehouse archive writer (page index, three row groups of 512/512/176 rows).
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const parquet = require('../src/parquet');
const { plan } = require('../src/plan');
const { simplify } = require('../src/simplify');

const FIXTURE = path.join(__dirname, 'fixtures', 'grid.parquet');
const open = () => parquet.open(FIXTURE);

test('the layer is described from the file metadata alone', async () => {
  const layer = await open();
  assert.equal(layer.rows, 1200);
  assert.equal(layer.geometryType, 'Polygon');
  assert.ok(layer.idIsRow);
  assert.deepEqual(layer.columns.map((c) => [c.name, c.type]),
    [['feature_id', 'Integer'], ['name', 'String'], ['sliprate', 'Double'], ['mapped', 'Date']]);
  assert.ok(layer.extent.xmin < -112 && layer.extent.xmax > -110.05);
});

test('a bbox selects only the rows whose bbox overlaps it', async () => {
  const layer = await open();
  const rows = await parquet.select(layer, plan({ geometry: '-111.96,40.04,-111.94,40.06', inSR: '4326' }, layer));
  assert.deepEqual(rows, [41]);   // the square at (-111.95, 40.05)
});

test('objectIds map to rows without reading the id column', async () => {
  const layer = await open();
  assert.deepEqual(await parquet.select(layer, plan({ objectIds: '3,1200,9999' }, layer)), [2, 1199]);
});

test('a page reads one extra row to know whether there is more', async () => {
  const layer = await open();
  const rows = await parquet.select(layer, plan({ resultRecordCount: '5', resultOffset: '1195' }, layer));
  assert.deepEqual(rows, [1195, 1196, 1197, 1198, 1199]);
});

test('records carry the planned columns and GeoJSON geometry', async () => {
  const layer = await open();
  const p = plan({ objectIds: '42', outFields: 'name' }, layer);
  const [r] = await parquet.records(layer, p, await parquet.select(layer, p));
  assert.equal(r.name, 'unit 41');
  assert.equal(Number(r.feature_id), 42);
  assert.equal(r.geom.type, 'Polygon');
});

test('simplification keeps rings closed and a speck of what collapses', () => {
  const square = { type: 'Polygon', coordinates: [[[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [0, 0, 0]]] };
  assert.deepEqual(simplify(square, 0.1).coordinates[0][0], [0, 0]);   // 2D
  assert.equal(simplify(square, 0.1).coordinates[0].length, 5);
  const circle = { type: 'Polygon', coordinates: [Array.from({ length: 33 }, (_, i) =>
    [Math.cos((i / 32) * 2 * Math.PI), Math.sin((i / 32) * 2 * Math.PI)])] };
  const [speck] = simplify(circle, 10).coordinates;   // its bounding box, not nothing
  assert.equal(speck.length, 5);
  assert.deepEqual([speck[0][0], speck[2][0]], [-1, 1]);
});

test('a bbox selection is cut to the page, plus one row to say there is more', async () => {
  const layer = await open();
  const p = plan({ geometry: '-113,39,-110,42', inSR: '4326' }, layer, 100);
  const rows = await parquet.select(layer, p);
  assert.equal(rows.length, 101);
  assert.deepEqual(rows.slice(0, 3), [0, 1, 2]);
});
