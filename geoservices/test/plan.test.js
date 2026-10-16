'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { plan, filterBox, fieldType } = require('../src/plan');

const layer = {
  url: 'https://cdn.example/x.parquet', geom: 'geom', bbox: 'flat', point: false,
  columns: [
    { name: 'feature_id', type: 'BIGINT' }, { name: 'faultname', type: 'VARCHAR' },
    { name: 'sliprate', type: 'DOUBLE' }, { name: 'mapped', type: 'TIMESTAMP' },
  ],
};

test('trivial count is answered by SQL without geometry', () => {
  const p = plan({ where: '1=1', returnCountOnly: 'true' }, layer);
  assert.equal(p.kind, 'count');
  assert.match(p.sql, /^SELECT count\(\*\)/);
});

test('a client where clause never reaches DuckDB', () => {
  const where = "faultname = 'x'; SELECT * FROM read_csv('/etc/passwd')";
  const p = plan({ where, outFields: '*' }, layer);
  assert.doesNotMatch(p.sql, /passwd|read_csv/);
  assert.deepEqual(p.filtersApplied, {});   // Koop applies where, so paging stays with Koop too
});

test('outFields and where-referenced columns are the only attributes read', () => {
  const p = plan({ where: "sliprate > 1", outFields: 'faultname' }, layer);
  assert.match(p.sql, /"faultname"/);
  assert.match(p.sql, /"sliprate"/);
  assert.doesNotMatch(p.sql, /"mapped"/);
});

test('paging is pushed down only for trivial where', () => {
  const p = plan({ where: '1=1', resultRecordCount: '10', resultOffset: '20', outFields: '*' }, layer);
  assert.match(p.sql, /ORDER BY feature_id LIMIT 11 OFFSET 20$/);
  assert.equal(p.limit, 10);
  assert.ok(p.filtersApplied.resultRecordCount);
});

test('a web mercator envelope becomes a lon/lat bbox prefilter on the covering columns', () => {
  const geometry = JSON.stringify({ xmin: -12485000, ymin: 4970000, xmax: -12445000, ymax: 5010000, spatialReference: { wkid: 102100 } });
  const box = filterBox({ geometry });
  assert.ok(box[0] < -112 && box[0] > -112.3 && box[1] > 40.6 && box[3] < 41.1);
  const p = plan({ geometry, geometryType: 'esriGeometryEnvelope', outFields: '*' }, layer);
  assert.match(p.sql, /bbox_xmax >= -112\.\d+ AND bbox_xmin <= -111\.\d+/);
  assert.equal(p.filtersApplied.resultRecordCount, undefined);   // Koop still filters exactly
});

test('point identify with a distance grows the box', () => {
  const box = filterBox({ geometry: '{"x":-112,"y":40}', inSR: '4326', distance: '1000', units: 'esriSRUnit_Meter' });
  assert.ok(box[2] - box[0] > 0.017);
});

test('unknown spatial references skip the prefilter rather than guess', () => {
  assert.equal(filterBox({ geometry: '{"x":1,"y":2}', inSR: '26912' }), null);
});

test('simplification follows maxAllowableOffset, in degrees for mercator output', () => {
  const p = plan({ maxAllowableOffset: '1113.2', outSR: '102100', outFields: 'faultname' }, layer);
  assert.match(p.sql, /ST_Simplify\(ST_Force2D\("geom"\), 0\.01\)/);
  assert.doesNotMatch(plan({ maxAllowableOffset: '10' }, { ...layer, point: true }).sql, /Simplify/);
});

test('objectIds must be integers', () => {
  assert.match(plan({ objectIds: '1,2' }, layer).sql, /feature_id IN \(1, 2\)/);
  assert.throws(() => plan({ objectIds: '1;DROP' }, layer));
});

test('column types map to the Esri field types the values are cast to', () => {
  assert.equal(fieldType({ name: 'feature_id', type: 'BIGINT' }), 'Integer');
  assert.equal(fieldType({ name: 'n', type: 'BIGINT' }), 'Double');
  assert.equal(fieldType({ name: 'd', type: 'TIMESTAMP WITH TIME ZONE' }), 'Date');
  assert.equal(fieldType({ name: 's', type: 'VARCHAR' }), 'String');
});
