# ugs-warehouse — GIS feature roadmap

Backlog of GIS capabilities beyond the current build. Grouped by where they fit our
**zero-server-lean** architecture: client-side (our wheelhouse — runs in the static viewer,
no infra) vs needs-a-service vs metadata/data-mgmt. Rough priority within each.

Status legend: ☐ not started · ◐ partial · ✅ done

---

## Shipped (baseline, 2026-06)
- ✅ STAC catalog (static, collections layout) + viewer (Catalog + Map)
- ✅ Vector serving: GeoParquet + PMTiles + DuckLake per topic
- ✅ Client-side export: SHP / GPKG / **FileGDB** / FlatGeobuf / GeoJSON / CSV (gdal3.js)
- ✅ Shareable deep-links · light/dark theme · OGC API Features (pg_featureserv, Esri-ready)
- ✅ Pub/Sub event ingest (dataELT #418)

---

## Tier 1 — client-side, high value (fits zero-server; build in the viewer)
- ☐ **Feature identify** — click a PMTiles feature → attribute popup. Biggest obvious miss.
- ☐ **Bbox / map-extent catalog filter** — filter items spatially (draw box / "in view"), not just text.
- ☐ **Clip-to-AOI download** — export only a bbox, not the whole layer (DuckDB already in-browser → add spatial WHERE).
- ☐ **Legend + symbology** — classed/categorical styling per layer + legend (today: single-color lines).
- ☐ **Basemap switcher** — satellite / topo / terrain (researched, not wired).
- ☐ **Multi-layer overlay** — stack/compare several topics (today: one item at a time).
- ☐ **Time slider** — for time-series rasters (soil-water model).
- ☐ **Map-extent permalink** — deep-link the camera, not just the item.
- ☐ Geocoder / place search · measure tool · coordinate readout.

## Tier 2 — needs a service
- ☐ **Raster tiles (titiler)** — dynamic COG tiling so rasters render in the viewer + WMTS. DevSeed-recommended. (Pairs with raster consumer / #169.)
- ☐ **WMS/WMTS** — legacy QGIS/Esri workflows; lost when GeoServer was killed. titiler covers raster WMTS; vector WMS would need something new.
- ☐ **OGC API Tiles** — vector tiles via API (tipg) vs our static PMTiles; only if consolidating Features+Tiles.
- ☐ **Native Esri FeatureServer REST** — we serve OGC API Features (ArcGIS reads it); add Esri's own REST only if an AGOL workflow demands it.
- ☐ **stac-geoparquet search** — scale catalog search past static-tree limits (+ stac-map UI).

## Tier 3 — metadata / data management
- ☐ **proj + raster + eo STAC extensions** — CRS, bands, EO metadata (today: web-map-links only).
- ☐ **ISO 19115 / FGDC metadata export** — formal metadata a state geological survey likely needs; STAC ≠ ISO.
- ☐ **Real collection extents + data-validity datetime** — extents are a hardcoded Utah bbox; `datetime` is ingest-time, not survey date.
- ☐ **DOI / citation** — dated GeoParquet archives are citable; no DOI minting.
- ☐ **Data dictionary** — per-layer field definitions.

## Tracked elsewhere
- Raster consumer COG promote (pending ugs-ingest #169) — see `RASTER_SPEC.md` / `HANDOFF.md`.
- Deploy / perms handoff — `DEPLOY.md`.
- DuckLake 2026 production-readiness — unverified; revisit.
