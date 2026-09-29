# UGS Warehouse — static STAC viewer

React + Vite + TanStack Query + react-map-gl/MapLibre. Reads the **static** STAC catalog
off the CDN (catalog.json → collections → items), reflects each item's properties, asset
download links, footprint geometry, and PMTiles layer (web-map-links). No server.

```bash
npm install
npm run dev                      # local: http://localhost:5173
npm run build                    # -> dist/ (static)
```

Catalog: defaults to the prod CDN path; override with `?catalog=<url>` (e.g. the
`warehouse-sandbox/stac/catalog.json`).

Views are real path routes (`/map`, `/discover`, `/catalog`, …), so every host has to rewrite an
unknown path to `index.html`: Firebase Hosting does it via `firebase.json`, the IAP review app and
the previews service via `serve.py`'s SPA fallback. A build mounted under a prefix must be told
which one — `npm run build -- --base=/review/viewer/` — because that base is also the router's
basepath (`src/lib/mount.ts`). Deploy is `firebase deploy --only hosting`; see `docs/DEPLOY.md` §5.

**Next:** DuckDB-WASM panel — search over stac-geoparquet + on-demand export (GPKG/SHP/GeoJSON), client-side.

<!-- preview smoke test, not for merge -->
