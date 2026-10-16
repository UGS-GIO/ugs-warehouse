// ArcGIS FeatureServer for the warehouse serving topics (README.md).
'use strict';

const Koop = require('@koopjs/koop-core');
const catalog = require('./catalog');
const provider = require('./provider');

const koop = new Koop({ logLevel: process.env.LOG_LEVEL ?? 'warn' });
koop.register(provider);

const baseUrl = (req) => process.env.PUBLIC_URL?.replace(/\/$/, '')
  ?? `${req.get('x-forwarded-proto') ?? req.protocol}://${req.get('host')}`;

// Every topic and render, with the URL to paste into Map Viewer's "Add layer from URL".
koop.server.get('/', async (req, res) => {
  try {
    const base = baseUrl(req);
    const out = [];
    for (const [id, item] of await catalog.topics()) {
      for (const [name, r] of Object.entries(catalog.rendersOf(item))) {
        out.push({ topic: id, render: name, title: r.title ?? item.properties?.title ?? id, styled: Boolean(r.esri_url),
          url: `${base}/ugs/rest/services/${encodeURIComponent(id)}/${encodeURIComponent(name)}/FeatureServer/0` });
      }
    }
    res.json({ layers: out });
  } catch (err) {
    res.status(502).json({ error: err.message });
  }
});

koop.server.get('/healthz', (_req, res) => res.json({ ok: true }));

const port = Number(process.env.PORT ?? 8080);
koop.server.listen(port, () => console.log(`geoservices listening on :${port}`));
