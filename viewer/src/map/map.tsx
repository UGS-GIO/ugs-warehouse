import { Toggle } from "@base-ui/react/toggle";
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useQuery } from "@tanstack/react-query";
import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { GeolocateControl, Layer, type LayerProps, type MapLayerMouseEvent, Map as MapGL, type MapRef, Popup, Source, type ViewStateChangeEvent } from "react-map-gl/maplibre";
import { ensureCogProtocol } from "./cog";
import { MapControl } from "./map-control";
import { ensurePmtilesProtocol } from "./pmtiles-protocol";
import { type StacDoc, useCogBoxes, useStyleLayersFor } from "@/stac";
import { usePerItem } from "@/lib/use-per-item";
import { UiSegmented } from "@/ui/segmented";
import { type ActiveLayer, colorForId, type Footprint, GEOM_FILTER, orderedSublayerIds, slugOf, validBbox } from "./map-model";
import { type Gate, gateOf, gateZoom, groupGate, useGatedOut, ZoomGateNotice } from "./zoomgate";

// deck.gl-zarr + luma.gl only load when a datacube is actually toggled on.
const ZarrOverlay = lazy(() => import("@/zarr/zarr-overlay").then((m) => ({ default: m.ZarrOverlay })));

ensurePmtilesProtocol();   // this module is lazy, so registration happens the first time a map loads

// Camera permalink: ?m=lng,lat,zoom (preserved alongside ?view/c/i/l).
type Cam = { longitude: number; latitude: number; zoom: number };
function readCam(): Cam | null {
  const m = new URLSearchParams(location.search).get("m");
  if (!m) return null;
  const [lng, lat, z] = m.split(",").map(Number);
  return Number.isFinite(lng) && Number.isFinite(lat) && Number.isFinite(z)
    ? { longitude: lng, latitude: lat, zoom: z } : null;
}
function writeCam({ longitude, latitude, zoom }: Cam): void {
  const p = new URLSearchParams(location.search);
  p.set("m", `${longitude.toFixed(4)},${latitude.toFixed(4)},${zoom.toFixed(2)}`);
  history.replaceState(null, "", `${location.pathname}?${p}`);
}

const ofm = (s: string) => `https://tiles.openfreemap.org/styles/${s}`;
// Ours, so it names glyphs itself (the OpenFreeMap basemaps bring their own) — else no labels (#116).
const SATELLITE: maplibregl.StyleSpecification = {
  version: 8,
  glyphs: "https://maps-assets.geology.utah.gov/styles/fonts/{fontstack}/{range}.pbf",
  sources: { sat: { type: "raster", tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"], tileSize: 256, attribution: "Imagery © Esri" } },
  layers: [{ id: "sat", type: "raster", source: "sat" }],
};
const BASEMAPS = {
  Streets: ofm("liberty"), Light: ofm("positron"), Satellite: SATELLITE,
} satisfies Record<string, string | maplibregl.StyleSpecification>;
type BasemapId = keyof typeof BASEMAPS;
const BASEMAP_ITEMS = (Object.keys(BASEMAPS) as BasemapId[]).map((value) => ({ value, label: value }));

// The footprint "Open item →" popup is the only popup left on the map — a data-feature click docks
// its detail instead (see SelectedFeature/onSelectFeature below), so this never carries feature props.
type PopupInfo = { lng: number; lat: number; title: string; href?: string };

// The related tables a clicked layer offers — names + the full-item href to resolve joins on open.
// Named from the compact index (cheap); the join columns are read from the full item only on open.
export type RelatedTablesInfo = { itemHref: string; tables: { key: string; title: string }[] };
// What "open a related table" hands back up to the map route (which renders it in the Info panel).
export type OpenRelated = { itemHref: string; relatedKey: string; title: string; props: Record<string, unknown> };
// A clicked data feature's detail, lifted to the route so it can be docked (no floating feature
// popup anywhere — the stakeholder-mandated pattern this replaces).
export type SelectedFeature = { title: string; props: Record<string, unknown>; related?: RelatedTablesInfo };

// Union of bboxes → [w,s,e,n], or null.
function unionBbox(bs: number[][]): [number, number, number, number] | null {
  if (!bs.length) return null;
  return [Math.min(...bs.map((b) => b[0])), Math.min(...bs.map((b) => b[1])),
          Math.max(...bs.map((b) => b[2])), Math.max(...bs.map((b) => b[3]))];
}

// A [w,s,e,n] bbox → a closed rectangle ring (GeoJSON Polygon coordinates).
const bboxRing = (b: number[]): GeoJSON.Position[][] =>
  [[[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]], [b[0], b[1]]]];

// All footprints → one FeatureCollection of bbox rectangles (properties carry href/title for the
// click-to-open + hover popup). Built once per footprints set; MapLibre handles thousands of rects.
function coverageFC(fps: Footprint[]): GeoJSON.FeatureCollection {
  return {
    type: "FeatureCollection",
    features: fps.map((f) => ({
      type: "Feature",
      properties: { href: f.href, title: f.title },
      geometry: { type: "Polygon", coordinates: bboxRing(f.bbox) },
    })),
  };
}

export function ItemMap({ item, layers, footprints = [], onPickFootprint,
  highlightBbox, onHoverFootprint, onBoundsChange, coverageDefault = false, relatedFor, onSelectFeature }: {
  item?: StacDoc; layers: ActiveLayer[];
  footprints?: Footprint[]; onPickFootprint?: (href: string) => void;
  // Related-table affordances: `relatedFor` maps a clicked layer id → its related tables (named
  // from the index by the caller). `onSelectFeature` lifts a clicked data feature up to the route,
  // which docks its detail — there is no floating feature popup. Both optional.
  relatedFor?: (layerId: string) => RelatedTablesInfo | undefined;
  onSelectFeature?: (f: SelectedFeature | null) => void;
  // Discovery sync (all optional — the map works standalone without them): a footprint to emphasize
  // (a hovered discovery card), a callback when a coverage footprint is hovered on the map (→ the
  // card list highlights it), and the viewport bounds after load/move (→ "Search this area").
  highlightBbox?: number[];
  onHoverFootprint?: (href: string | null) => void;
  onBoundsChange?: (bbox: [number, number, number, number]) => void;
  // Start with the coverage overlay ON. The Discover view leaves it default (footprints ARE its
  // point); the Map view omits it, so coverage stays opt-in there as before.
  coverageDefault?: boolean;
}) {
  const mapRef = useRef<MapRef>(null);
  const [mapLoaded, setMapLoaded] = useState(false);
  const [cursor, setCursor] = useState<"" | "pointer">("");
  const [popup, setPopup] = useState<PopupInfo | null>(null);
  // The selected feature's geometry, highlighted on the map so switching the dock (e.g. to open a
  // related table) doesn't lose your place. From the click event's own geometry — tile-clipped for
  // very large polygons, but accurate enough for a highlight.
  // Scoped to the shown item: a new item reads back as null in the same render (no reset-effect
  // frame where the old item's highlight would paint). See lib/use-per-item.ts.
  const [hlGeom, setHlGeom] = usePerItem<GeoJSON.Geometry | null>(item?.id ?? "", null);
  const [basemap, setBasemap] = useState<BasemapId>("Streets");
  // The discovery highlight rectangle: the hovered card's footprint, normalized (validBbox handles a
  // 6-length 3D bbox and rejects bad values) so a malformed bbox just draws nothing.
  const highlight = validBbox(highlightBbox);
  // Report the viewport bbox on load + after every move, for the panel's "Search this area".
  const reportBounds = () => {
    const m = mapRef.current?.getMap();
    if (!m || !onBoundsChange) return;
    const b = m.getBounds();
    onBoundsChange([b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]);
  };
  // Map → card: when the cursor is over a coverage footprint, report its href (deduped via a ref so
  // a continuous mousemove doesn't spam state). Only fires when the coverage overlay is shown.
  const hoveredHref = useRef<string | null>(null);
  const emitHover = (href: string | null) => {
    if (href === hoveredHref.current) return;
    hoveredHref.current = href;
    onHoverFootprint?.(href);
  };
  const onHover = (e: MapLayerMouseEvent) => {
    if (!onHoverFootprint) return;
    const f = e.features?.find((ft) => ft.layer.id === "coverage-fill");
    emitHover(f?.properties?.href ? String(f.properties.href) : null);
  };
  // Coverage overlay (all item footprints as clickable rectangles) — OFF by default: on, every
  // item in the open collection draws a rectangle whether or not its layer is on, so turning a
  // layer off still left something on the map. Opt in from the toggle when you want the "what is
  // mapped where" view.
  const [showCoverage, setShowCoverage] = useState(coverageDefault);
  const coverage = showCoverage && footprints.length ? coverageFC(footprints) : null;
  // COG (raster) layers need the cog:// protocol registered before their Source mounts. Register
  // lazily the first time any toggled-on layer is a COG; render those Sources only once ready.
  const hasCog = layers.some((l) => l.cogHref);
  // Registering the cog:// protocol is a one-time async import. As a query it needs no ready flag and
  // no liveness guard: the cache holds the resolved state, so a late resolve can't set state on an
  // unmounted map, and every map that mounts later reads it as already done.
  const { isSuccess: cogReady } = useQuery({
    queryKey: ["cog-protocol"],
    queryFn: async () => { await ensureCogProtocol(); return true as const; },
    enabled: hasCog, staleTime: Infinity, gcTime: Infinity,
  });
  // Datacubes render through deck.gl, not a maplibre Source, so they're collected here and drawn by
  // one overlay rather than in the per-layer Source switch below.
  const zarrSpecs = layers.flatMap((l) => (l.zarr ? [{ id: l.id, ...l.zarr }] : []));
  // COG extents for camera fit (many pub/raster items have no STAC bbox), one cached query per href.
  const cogBoxes = useCogBoxes(layers.map((l) => l.cogHref));

  // A layer's effective bbox: its STAC bbox, else (for a COG) its fetched GeoTIFF extent.
  const effBox = (l: ActiveLayer): [number, number, number, number] | undefined => {
    const b = l.bbox?.slice(0, 4);
    if (b && b.length >= 4) return b as [number, number, number, number];
    return l.cogHref ? cogBoxes[l.cogHref] : undefined;
  };
  const initialCam = useRef(readCam());
  const lastFit = useRef<string | null>(null);
  const honorCam = useRef(Boolean(initialCam.current));

  // Fit to the union of active layers when the set changes (StrictMode-safe via lastFit;
  // a shared ?m= camera wins over the first auto-fit).
  const activeBoxes = layers.map(effBox).filter((b): b is [number, number, number, number] => Array.isArray(b));
  // `|N` so the effect re-fires when an async COG extent arrives (activeBoxes grows) and re-fits.
  const fitKey = (layers.map((l) => l.id).join(",") || (item?.bbox?.join(",") ?? "")) + `|${activeBoxes.length}`;
  const fitBox = unionBbox(activeBoxes) ?? (item?.bbox?.slice(0, 4) as [number, number, number, number] | undefined);
  useEffect(() => {
    if (!fitKey || !fitBox || !mapRef.current || fitKey === lastFit.current) return;
    lastFit.current = fitKey;
    setPopup(null);
    if (honorCam.current) { honorCam.current = false; return; }
    const [w, s, e, n] = fitBox;
    mapRef.current.fitBounds([[w, s], [e, n]], { padding: 40, maxZoom: 12, duration: 600 });
  }, [fitKey]);

  // Bound GL style `layers` per overlay (id→layers for those that resolved), via TanStack Query.
  const styleCache = useStyleLayersFor(layers.map((l) => ({ id: l.id, styleUrl: l.styleUrl })));

  // Restack the canvas to match the tray order. react-map-gl only calls moveLayer when a <Layer>'s
  // beforeId prop changes, and we pass none — so a drag-reorder rewrites ?l= and re-renders the
  // layers in the new order but never restacks the drawn features. Reconcile imperatively:
  // moveLayer(id) with no `before` sends a layer to the top, so walking the desired stack bottom→top
  // lands each above the last and the final order matches the tray. We deliberately avoid a computed
  // beforeId — maplibre's moveLayer splices the layer OUT of the draw order before checking the
  // before-target exists, so a beforeId pointing at a not-yet-loaded layer during an async window can
  // drop a layer with no retry. Tray top row = front (drawn on top), so reverse to get bottom→top.
  const orderKey = layers.map((l) => l.id).join(",");
  const styledKey = layers.map((l) => styleCache[l.id]?.length ?? 0).join(",");
  useEffect(() => {
    const map = mapRef.current?.getMap();
    if (!map) return;
    const reconcile = () => {
      if (!map.isStyleLoaded()) return;
      const desired = orderedSublayerIds([...layers].reverse(), {
        styledCount: (id) => styleCache[id]?.length,
        cogReady,
      }).filter((id) => map.getLayer(id));
      // `idle` fires on every pan/zoom — skip the moveLayer churn when the stack is already correct.
      const current = map.getStyle().layers.map((l) => l.id).filter((id) => desired.includes(id));
      if (current.length === desired.length && current.every((id, i) => id === desired[i])) return;
      for (const id of desired) map.moveLayer(id);
    };
    reconcile();
    map.on("idle", reconcile);
    return () => { map.off("idle", reconcile); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [orderKey, styledKey, cogReady, mapLoaded]);

  // Source/layer ids use slugOf(id) (imported) — a stable slug, never the array index, so unchecking
  // a layer can't rename a mounted source (maplibre throws "source id changed" and takes the map down).
  const layerByMapId: Record<string, ActiveLayer> = {};
  const interactiveIds = layers.flatMap((l) => {
    const s = slugOf(l.id);
    const styleLayers = styleCache[l.id];
    const ids = styleLayers
      ? styleLayers.map((_, li) => `pm-${s}-${li}`)
      : [`pm-${s}-fill`, `pm-${s}-line`, `pm-${s}-circle`];
    for (const mid of ids) layerByMapId[mid] = l;
    return ids;
  });
  // Coverage rectangles are clickable too (identify → open). Listed last so data-layer features
  // win the topmost-hit when they overlap a footprint.
  const allInteractiveIds = coverage ? [...interactiveIds, "coverage-fill"] : interactiveIds;

  // Scale-gated overlays. A toggled-on layer whose style only draws deeper in (PLSS sections:
  // z≥11.13) is blank at the statewide opening view, which reads as "this layer is broken" — the
  // more so because an UNSTYLED layer falls back to paint with no gate and does draw. Name them
  // instead. Only layers whose style has resolved carry a gate, so nothing flashes during load.
  const gateById: Record<string, Gate | null> = {};
  for (const l of layers) gateById[l.id] = gateOf(styleCache[l.id]);
  const gatedOut = useGatedOut(mapRef, layers.map((l) => ({ id: l.id, gate: gateById[l.id] })), mapLoaded);
  const hiddenGroup = (dir: "in" | "out") =>
    groupGate(layers.filter((l) => gatedOut[l.id] === dir).map((l) => ({ title: l.title, gate: gateById[l.id] })), dir);
  // Zoom-in wins the slot when both directions are gated — it's the case that actually occurs, since
  // SLD MaxScaleDenominators become minzooms.
  const inGroup = hiddenGroup("in");
  const hidden = inGroup ?? hiddenGroup("out");
  const hiddenDir: "in" | "out" = inGroup ? "in" : "out";
  // Zoom only — the centre is where the user is looking, and fitting a statewide layer's bounds
  // would land on one arbitrary feature.
  const easeZoomTo = (z: number) => mapRef.current?.getMap().easeTo({ zoom: z, duration: 600 });

  const onClick = (e: MapLayerMouseEvent) => {
    const f = e.features?.[0];
    if (!f) { setPopup(null); onSelectFeature?.(null); setHlGeom(null); return; }
    if (f.layer.id === "coverage-fill") {
      // A footprint: popup its title + a link to open the item (don't yank the user off the map).
      // Clear any docked FeatureDetail too — a footprint popup and a docked feature must never
      // coexist (that's the floating-popup-beside-the-dock drift this redesign removes).
      setPopup({ lng: e.lngLat.lng, lat: e.lngLat.lat, title: String(f.properties?.title ?? ""),
                 href: f.properties?.href ? String(f.properties.href) : undefined });
      onSelectFeature?.(null);
      setHlGeom(null);   // a footprint isn't a data feature — nothing to highlight
      return;
    }
    const l = layerByMapId[f.layer.id];
    // Resolve the related tables ONCE here, not on every dock render (ItemMap re-renders on hover/move).
    // Lifted to the route, which docks the detail — no floating feature popup. Close any open
    // footprint popup too, for the same reason as above.
    onSelectFeature?.({ title: l?.title ?? "", props: f.properties ?? {},
                         related: l?.id ? relatedFor?.(l.id) : undefined });
    setPopup(null);
    setHlGeom((f.geometry as GeoJSON.Geometry) ?? null);
  };

  return (
    <MapGL
      ref={mapRef}
      mapLib={maplibregl}
      initialViewState={initialCam.current ?? { longitude: -111.7, latitude: 39.3, zoom: 5.3 }}
      mapStyle={BASEMAPS[basemap]}
      style={{ width: "100%", height: "100%" }}
      interactiveLayerIds={allInteractiveIds}
      cursor={cursor}
      onMouseEnter={() => setCursor("pointer")}
      onMouseLeave={() => { setCursor(""); emitHover(null); }}
      onMouseMove={onHover}
      onLoad={() => { setMapLoaded(true); reportBounds(); }}
      onMoveEnd={(e: ViewStateChangeEvent) => { writeCam(e.viewState); reportBounds(); }}
      onClick={onClick}
    >
      <MapControl position="top-left">
        <Geocoder onPick={(b) => mapRef.current?.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: 40, maxZoom: 14, duration: 800 })} />
      </MapControl>

      {/* Added before the geolocate control, so it sits above it in the same corner. */}
      <MapControl position="top-right" className="flex gap-1 text-xs">
        <UiSegmented value={basemap} onValueChange={setBasemap} items={BASEMAP_ITEMS}
          className="bg-card/95 shadow" />
        {footprints.length > 0 && (
          <Toggle pressed={showCoverage} onPressedChange={setShowCoverage}
            title="Show every item's footprint (what's mapped where)"
            className="cursor-pointer select-none rounded-md border border-input bg-card/95 px-2 py-1 text-foreground shadow hover:bg-hover data-[pressed]:bg-primary data-[pressed]:text-primary-foreground">
            Coverage · {footprints.length}
          </Toggle>
        )}
      </MapControl>

      <GeolocateControl position="top-right" trackUserLocation
        positionOptions={{ enableHighAccuracy: true }} />

      {/* Scale-gated overlays: name the layers this zoom hides, and offer the one move that reveals
          them all. */}
      {hidden && (
        <MapControl position="bottom-left">
          <ZoomGateNotice gate={hidden.gate} dir={hiddenDir} subject={hidden.subject}
            onZoom={() => easeZoomTo(gateZoom(hidden.gate, hiddenDir))} />
        </MapControl>
      )}

      {/* Coverage overlay — all item footprints as clickable rectangles, beneath the data layers so
          those stay on top. Very light fill; the outline is what reads as "here's a mapped area". */}
      {coverage && (
        <Source id="coverage" type="geojson" data={coverage}>
          <Layer id="coverage-fill" type="fill" paint={{ "fill-color": "#2b6cdf", "fill-opacity": 0.05 }} />
          <Layer id="coverage-line" type="line" paint={{ "line-color": "#2b6cdf", "line-width": 0.7, "line-opacity": 0.55 }} />
        </Source>
      )}

      {/* Discovery highlight — the hovered card's footprint, emphasized (orange, above coverage).
          Non-interactive, so it never steals clicks/hover from the data layers or coverage. */}
      {highlight && (
        <Source id="discovery-highlight" type="geojson"
          data={{ type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: bboxRing(highlight) } }}>
          <Layer id="discovery-highlight-fill" type="fill" paint={{ "fill-color": "#d1491c", "fill-opacity": 0.12 }} />
          <Layer id="discovery-highlight-line" type="line" paint={{ "line-color": "#d1491c", "line-width": 2.5 }} />
        </Source>
      )}

      {item?.geometry && (
        <Source id="footprint" type="geojson" data={{ type: "Feature", properties: {}, geometry: item.geometry }}>
          <Layer id="fp-line" type="line" paint={{ "line-color": "#888", "line-width": 1, "line-dasharray": [2, 2] }} />
        </Source>
      )}

      {zarrSpecs.length > 0 && (
        <Suspense fallback={null}><ZarrOverlay specs={zarrSpecs} /></Suspense>
      )}

      {layers.map((l) => {
        const s = slugOf(l.id);
        if (l.zarr) return null;   // drawn by the deck overlay above
        // Raster PMTiles mosaic — the per-scale geologic-map tiles, served via the already-registered
        // pmtiles:// protocol as a raster source. No styling: it's the published map image.
        if (l.rasterPmHref) {
          return (
            <Source key={l.id} id={`rpm-${s}`} type="raster" url={`pmtiles://${l.rasterPmHref}`} tileSize={256}>
              <Layer id={`rpm-${s}-raster`} type="raster" paint={{ "raster-opacity": 1 }} />
            </Source>
          );
        }
        // Raster COG layer — render the georeferenced GeoTIFF via cog:// (once the protocol is
        // registered). Distinct `cog-*` ids keep it out of the vector feature-click regex.
        if (l.cogHref) {
          if (!cogReady) return null;
          return (
            <Source key={l.id} id={`cog-${s}`} type="raster" url={`cog://${l.cogHref}`} tileSize={256}>
              <Layer id={`cog-${s}-raster`} type="raster" paint={{ "raster-opacity": 0.9 }} />
            </Source>
          );
        }
        const c = colorForId(l.id);  // keyed to id → matches the legend/tray swatch and survives reorder
        const styleLayers = styleCache[l.id];
        const pmHref = l.pmHref!;
        const pmLayer = l.pmLayer!;
        return (
          <Source key={l.id} id={`pm-${s}`} type="vector" url={`pmtiles://${pmHref}`}>
            {styleLayers ? (
              styleLayers.map((sl, li) => (
                <Layer
                  key={li}
                  {...({
                    ...sl,
                    id: `pm-${s}-${li}`,
                    source: `pm-${s}`,
                    "source-layer": pmLayer,
                  } as LayerProps)}
                />
              ))
            ) : (
              // An array, NOT a fragment: Source clones each child to inject `source`, and cloning a
              // fragment puts the prop on the fragment (three React warnings per layer, and no
              // source on the layers). Explicit `source` for the same reason — don't rely on inject.
              [
                <Layer key="fill" id={`pm-${s}-fill`} source={`pm-${s}`} type="fill" source-layer={pmLayer} filter={GEOM_FILTER.fill} paint={{ "fill-color": c, "fill-opacity": 0.15 }} />,
                <Layer key="line" id={`pm-${s}-line`} source={`pm-${s}`} type="line" source-layer={pmLayer} filter={GEOM_FILTER.line} paint={{ "line-color": c, "line-width": 1.2 }} />,
                <Layer key="circle" id={`pm-${s}-circle`} source={`pm-${s}`} type="circle" source-layer={pmLayer} filter={GEOM_FILTER.point} paint={{ "circle-color": c, "circle-radius": 3, "circle-opacity": 0.85 }} />,
              ]
            )}
          </Source>
        );
      })}

      {/* Selected-feature highlight — marks which feature is docked, and survives switching the dock
          (e.g. to open a related table) so you don't lose your place. Non-interactive. */}
      {hlGeom && (
        <Source id="feat-hl" type="geojson" data={{ type: "Feature", properties: {}, geometry: hlGeom }}>
          <Layer id="feat-hl-fill" type="fill" paint={{ "fill-color": "#f59e0b", "fill-opacity": 0.25 }} />
          <Layer id="feat-hl-line" type="line" paint={{ "line-color": "#f59e0b", "line-width": 3 }} />
          <Layer id="feat-hl-pt" type="circle" filter={["in", ["geometry-type"], ["literal", ["Point", "MultiPoint"]]]} paint={{ "circle-radius": 7, "circle-color": "#f59e0b", "circle-stroke-color": "#fff", "circle-stroke-width": 2 }} />
        </Source>
      )}

      {/* Footprint discovery affordance only — a data-feature click docks its detail (onSelectFeature
          above) instead of popping up. */}
      {popup && (
        <Popup longitude={popup.lng} latitude={popup.lat} onClose={() => setPopup(null)} closeButton maxWidth="320px">
          {popup.title && <div className="mb-1 text-xs font-semibold text-gray-900">{popup.title}</div>}
          {popup.href && (
            <button onClick={() => { onPickFootprint?.(popup.href!); setPopup(null); }}
              className="text-xs font-medium text-primary underline underline-offset-2 hover:opacity-80">
              Open item →
            </button>
          )}
        </Popup>
      )}
    </MapGL>
  );
}

// Keyless place search via Nominatim (OSM). US-biased; flies the map to the first hit.
function Geocoder({ onPick }: { onPick: (b: [number, number, number, number]) => void }) {
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string>();

  const search = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!q.trim()) return;
    setBusy(true);
    setErr(undefined);
    try {
      const u = `https://nominatim.openstreetmap.org/search?format=jsonv2&limit=1&countrycodes=us&q=${encodeURIComponent(q)}`;
      const hits = await (await fetch(u)).json();
      if (!hits.length) { setErr("not found"); return; }
      const bb = hits[0].boundingbox.map(Number); // [south, north, west, east]
      onPick([bb[2], bb[0], bb[3], bb[1]]); // → [w, s, e, n]
    } catch {
      setErr("search failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={search} className="flex items-center gap-1 rounded-md border border-border bg-card/95 p-1 text-xs shadow">
      <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search place…"
        className="w-24 sm:w-40 rounded bg-transparent px-1.5 py-0.5 text-foreground placeholder:text-muted-foreground focus:outline-none" />
      <button type="submit" disabled={busy} className="rounded bg-primary px-2 py-0.5 text-primary-foreground disabled:opacity-50">
        {busy ? "…" : "Go"}
      </button>
      {err && <span className="px-1 text-destructive">{err}</span>}
    </form>
  );
}

function FeatureProps({ props }: { props: Record<string, unknown> }) {
  const rows = Object.entries(props).filter(([, v]) => v !== null && v !== "").slice(0, 18);
  if (!rows.length) return <em className="text-muted-foreground">No attributes.</em>;
  // Flow the pairs into columns so a wide (desktop) dock fills its horizontal space instead of a
  // narrow table hugging the left edge; collapses to one column in the narrow mobile sheet.
  return (
    <dl className="columns-1 gap-x-8 text-xs sm:columns-2 lg:columns-3">
      {rows.map(([k, v]) => (
        <div key={k} className="flex break-inside-avoid items-baseline gap-2 py-0.5">
          <dt className="shrink-0 whitespace-nowrap font-medium text-muted-foreground">{k}</dt>
          <dd className="min-w-0 flex-1 truncate text-foreground" title={String(v)}>{String(v)}</dd>
        </div>
      ))}
    </dl>
  );
}

// Related-table affordances under the feature props. Names come straight from the index (no fetch on
// click); the join columns, COUNT, and rows load only when one is opened, in the Info panel.
function RelatedLinks({ info, props, onOpen }: {
  info?: RelatedTablesInfo; props: Record<string, unknown>; onOpen: (r: OpenRelated) => void;
}) {
  if (!info?.tables.length) return null;
  return (
    <div className="mt-3 border-t border-border pt-2.5">
      <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">Related tables</div>
      <div className="flex flex-wrap gap-1.5">
        {info.tables.map((t) => (
          <button key={t.key} type="button"
            onClick={() => onOpen({ itemHref: info.itemHref, relatedKey: t.key, title: t.title, props })}
            className="inline-flex items-center gap-1 rounded-md border border-border bg-card px-2.5 py-1 text-xs font-medium text-foreground transition-colors hover:border-primary/40 hover:bg-primary/5 hover:text-primary">
            {t.title} <span aria-hidden className="text-primary">→</span>
          </button>
        ))}
      </div>
    </div>
  );
}

// Docked feature detail — a clicked data feature's props + related-table launchers, rendered in the
// Info dock (in the dock's overflow-auto slot, so no fixed height needed). Replaces the old floating
// feature popup entirely; the footprint "Open item →" popup above is a separate, unrelated affordance.
export function FeatureDetail({ feature, onOpenRelated, onClose }: {
  feature: SelectedFeature; onOpenRelated: (r: OpenRelated) => void; onClose: () => void;
}) {
  return (
    <div>
      <div className="mb-2.5 flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">Selected feature</div>
          <h3 className="truncate text-sm font-semibold text-foreground" title={feature.title}>{feature.title || "Feature"}</h3>
        </div>
        <button type="button" onClick={onClose} aria-label="Close feature detail" title="Close feature detail"
          className="-mr-1 shrink-0 rounded p-1 text-muted-foreground hover:text-foreground"><span aria-hidden>✕</span></button>
      </div>
      <FeatureProps props={feature.props} />
      <RelatedLinks info={feature.related} props={feature.props} onOpen={onOpenRelated} />
    </div>
  );
}
