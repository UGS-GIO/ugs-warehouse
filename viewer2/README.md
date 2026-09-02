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
`warehouse-sandbox/stac/catalog.json`). Deploy by uploading `dist/` to the bucket → served via CDN.

**Next:** DuckDB-WASM panel — search over stac-geoparquet + on-demand export (GPKG/SHP/GeoJSON), client-side.
