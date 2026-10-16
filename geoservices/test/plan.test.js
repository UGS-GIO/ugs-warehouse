'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { plan, filterBox } = require('../src/plan');

const layer = {
  point: false,
  columns: [{ name: 'feature_id' }, { name: 'faultname' }, { name: 'sliprate' }, { name: 'mapped' }],
};

test('trivial counts and extents come from the file metadata', () => {
  assert.equal(plan({ where: '1=1', returnCountOnly: 'true' }, layer).kind, 'count');
  assert.equal(plan({ returnExtentOnly: 'true', outSR: '102100' }, layer).kind, 'extent');
  // a filtered count must read rows
  assert.equal(plan({ where: "sliprate > 1", returnCountOnly: 'true' }, layer).kind, 'features');
});

test('outFields and where-referenced columns are the only attributes read', () => {
  const p = plan({ where: 'sliprate > 1', outFields: 'faultname' }, layer);
  assert.deepEqual(p.columns, ['feature_id', 'faultname', 'sliprate']);
});

test('paging is ours only for trivial where in feature_id order', () => {
  const p = plan({ where: '1=1', resultRecordCount: '10', resultOffset: '20' }, layer);
  assert.deepEqual(p.page, { offset: 20, limit: 10 });
  assert.ok(p.filtersApplied.resultRecordCount);
  assert.equal(plan({ where: "faultname = 'x'", resultRecordCount: '10' }, layer).page, null);
  assert.equal(plan({ resultRecordCount: '10', orderByFields: 'faultname DESC' }, layer).page, null);
});

test('a web mercator envelope becomes a lon/lat bbox prefilter', () => {
  const geometry = JSON.stringify({ xmin: -12485000, ymin: 4970000, xmax: -12445000, ymax: 5010000, spatialReference: { wkid: 102100 } });
  const [x0, y0, , y1] = filterBox({ geometry });
  assert.ok(x0 < -112 && x0 > -112.3 && y0 > 40.6 && y1 < 41.1);
  assert.equal(plan({ geometry, outFields: '*' }, layer).page, null);   // Koop still filters exactly
});

test('point identify with a distance grows the box', () => {
  const box = filterBox({ geometry: '{"x":-112,"y":40}', inSR: '4326', distance: '1000', units: 'esriSRUnit_Meter' });
  assert.ok(box[2] - box[0] > 0.017);
});

test('unknown spatial references skip the prefilter rather than guess', () => {
  assert.equal(filterBox({ geometry: '{"x":1,"y":2}', inSR: '26912' }), null);
});

test('simplification follows maxAllowableOffset, in degrees for mercator output', () => {
  assert.equal(plan({ maxAllowableOffset: '1113.2', outSR: '102100' }, layer).tolerance, 0.01);
  assert.equal(plan({ maxAllowableOffset: '10' }, { ...layer, point: true }).tolerance, 0);
  assert.equal(plan({ maxAllowableOffset: '10', returnGeometry: 'false' }, layer).tolerance, 0);
});

test('objectIds must be integers', () => {
  assert.deepEqual(plan({ objectIds: '1,2' }, layer).ids, [1, 2]);
  assert.throws(() => plan({ objectIds: '1;DROP' }, layer), /integers/);
});
