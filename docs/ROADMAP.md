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
- ✅ **Feature identify** — click a feature → attribute popup (fill/line/circle render makes all geom types clickable).
- ✅ **Clip-to-AOI download** — export only a bbox (DuckDB `ST_Intersects` filter), prefilled from item extent.
- ✅ **Basemap switcher** — streets / light / satellite (keyless: OpenFreeMap + Esri imagery).
- ☐ **Bbox / map-extent catalog filter** — filter items spatially (draw box / "in view"), not just text.
- ◐ **Legend + symbology** — classed/categorical styling per layer + legend (today: single-color lines). Design: [STYLING.md](STYLING.md) — bind ugs-styles → STAC `renders` by item id; viewers consume.
- ✅ **Map-extent permalink** — `?m=lng,lat,zoom` restores the exact camera (auto-fit only on item change).
- ✅ **Multi-layer overlay** — toggle/compare several topics at once, per-layer color, identify across all, `?l=` set.
- ☐ **Time slider** — for time-series rasters (soil-water model).
- ✅ **Geocoder** — place search via Nominatim (keyless, US-biased), flies to result.
- ☐ measure tool · coordinate readout.

Mobile: ✅ responsive layout (map view stacks, header trims, tables scroll-x).

## Tier 2 — needs a service
- ✅ **COG in the viewer (client-side)** — interactive pan/zoom of the actual COG via the `cog://` MapLibre protocol (browser range-reads + geotiff.js, lazy-loaded). Zero-server; one COG at a time. Shows real pixels, no invented styling.
- ☐ **Raster tiles (titiler)** — dynamic COG tiling + WMTS, for scale/odd-projection COGs the client decoder can't handle + legacy WMTS consumers. DevSeed-recommended. (Client `cog://` covers in-viewer render today.)
- ☐ **WMS/WMTS** — legacy QGIS/Esri workflows; lost when GeoServer was killed. titiler covers raster WMTS; vector WMS would need something new.
- ☐ **OGC API Tiles** — vector tiles via API (tipg) vs our static PMTiles; only if consolidating Features+Tiles.
- ☐ **Native Esri FeatureServer REST** — we serve OGC API Features (ArcGIS reads it); add Esri's own REST only if an AGOL workflow demands it.
- ☐ **stac-geoparquet search** — scale catalog search past static-tree limits (+ stac-map UI).

## Tier 3 — metadata / data management
- ◐ **STAC extensions** — ✅ `proj` (proj:epsg on items); ☐ raster bands / eo metadata.
- ◐ **ISO 19115 / FGDC metadata export** — ✅ ISO 19139 sidecar per **vector + pubs** item (shared `core.stac.attach_iso`), linked as a `metadata` asset, for gov clearinghouses; ☐ FGDC CSDGM variant + extend to raster (after #169).
- ◐ **Real collection extents + data-validity datetime** — ✅ extents now derived from items (bbox union + temporal interval); ☐ `datetime` still ingest-time (no upstream validity timestamp available yet).
- ☐ **DOI / citation** — dated GeoParquet archives are citable; no DOI minting.
- ☐ **Data dictionary** — per-layer field definitions.

## Ops / orchestration (deferred — not a now-need)
- ☐ **Job orchestration** — sequence/schedule the Cloud Run Jobs (nightly reingest → refresh_catalog;
  harvest → promote → STAC; raster on #169). **Cloud Workflows + Cloud Scheduler** (serverless,
  pay-per-run, ephemeral) — NOT Cloud Composer/Airflow (~$300–500/mo always-on, overkill). Authorable
  as YAML locally, deploy on work box. **Defer until:** #169 raster jobs add dependencies, or
  soil-water time-series needs scheduled daily appends. Today the event path (#418 Pub/Sub) already
  covers recurring per-topic ingest; batch jobs are rare + run by hand.

## Tracked elsewhere
- Raster consumer COG promote (pending ugs-ingest #169) — see `RASTER_SPEC.md` / `HANDOFF.md`.
- Deploy / perms handoff — `DEPLOY.md`.
- DuckLake 2026 production-readiness — unverified; revisit.
