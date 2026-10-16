// What is served and how it looks, read from the STAC catalog on demand (derive-from-truth).
//
// A layer is a serving topic with a GeoParquet `data` asset; each of its `ugs:renders` is its
// own FeatureServer, symbolized by the Esri renderer ugs-styles publishes beside the GL style
// (`esri_url`). A topic with no renders is served once, as `default`, with Koop's symbol.
'use strict';

const fs = require('node:fs/promises');

const COLLECTION_URL = process.env.STAC_COLLECTION_URL
  ?? 'https://maps-assets.geology.utah.gov/warehouse/stac/ugs-serving-topics/items.json';
const CACHE_TTL = Number(process.env.CACHE_TTL ?? 300) * 1000;

const cache = new Map();

// TTL memo. Stale-on-error: a CDN blip serves the last good value rather than failing.
async function cached(key, produce) {
  const hit = cache.get(key);
  if (hit && Date.now() - hit.at < CACHE_TTL) return hit.value;
  try {
    const value = await produce();
    cache.set(key, { at: Date.now(), value });
    return value;
  } catch (err) {
    if (hit) return hit.value;
    throw err;
  }
}

async function getJson(url) {
  if (!/^https?:/.test(url)) return JSON.parse(await fs.readFile(url, 'utf8'));
  const res = await fetch(url, { signal: AbortSignal.timeout(10_000) });
  if (!res.ok) throw Object.assign(new Error(`${res.status} for ${url}`), { code: res.status });
  return res.json();
}

const topics = () => cached('topics', async () => {
  const doc = await getJson(COLLECTION_URL);
  return new Map((doc.items ?? [])
    .filter((item) => item.assets?.data?.href)
    .map((item) => [item.id, item]));
});

async function topic(id) {
  const item = (await topics()).get(id);
  if (!item) throw Object.assign(new Error(`unknown topic: ${id}`), { code: 404 });
  return item;
}

const rendersOf = (item) => {
  const renders = item.properties?.['ugs:renders'] ?? {};
  return Object.keys(renders).length ? renders : { default: {} };
};

async function render(item, name) {
  const renders = rendersOf(item);
  if (!(name in renders)) {
    throw Object.assign(new Error(`unknown render '${name}' for ${item.id} (have: ${Object.keys(renders).join(', ')})`), { code: 404 });
  }
  const url = renders[name].esri_url;
  return url ? cached(`esri:${url}`, () => getJson(url)) : {};
}

module.exports = { topics, topic, rendersOf, render, cached };
