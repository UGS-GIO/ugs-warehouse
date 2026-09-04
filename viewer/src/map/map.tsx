import { Toggle } from "@base-ui/react/toggle";
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { Layer, type LayerProps, type MapLayerMouseEvent, Map as MapGL, type MapRef, Popup, Source, type ViewStateChangeEvent } from "react-map-gl/maplibre";
import { ensureCogProtocol } from "./cog";
import { ensurePmtilesProtocol } from "./pmtiles-protocol";
import { type StacDoc, useCogBoxes, useStyleLayersFor } from "../stac";
import { UiSegmented } from "../ui/segmented";
import { type ActiveLayer, colorFor, type Footprint, validBbox } from "./map-model";
import { type Gate, gateOf, gateZoom, groupGate, useGatedOut, ZoomGateNotice } from "./zoomgate";

// deck.gl-zarr + luma.gl only load when a datacube is actually toggled on.
const ZarrOverlay = lazy(() => import("../zarr/zarr-overlay").then((m) => ({ default: m.ZarrOverlay })));

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

type PopupInfo = { lng: number; lat: number; title: string; props: Record<string, unknown>; href?: string };

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
  highlightBbox, onHoverFootprint, onBoundsChange, coverageDefault = false }: {
  item?: StacDoc; layers: ActiveLayer[];
  footprints?: Footprint[]; onPickFootprint?: (href: string) => void;
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
  const [cogReady, setCogReady] = useState(false);
  const hasCog = layers.some((l) => l.cogHref);
  // Datacubes render through deck.gl, not a maplibre Source, so they're collected here and drawn by
  // one overlay rather than in the per-layer Source switch below.
  const zarrSpecs = layers.flatMap((l) => (l.zarr ? [{ id: l.id, ...l.zarr }] : []));
  useEffect(() => {
    if (!hasCog || cogReady) return;
    let live = true;
    ensureCogProtocol().then(() => { if (live) setCogReady(true); });
    return () => { live = false; };
  }, [hasCog, cogReady]);

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

  // Source/layer ids are keyed by a STABLE slug of the layer id — NOT the array index. Index-based
  // ids change when a layer is unchecked (the array shifts), and react-map-gl throws "source id
  // changed" (you can't rename a mounted maplibre source) → the page crashes. Slugs stay constant.
  const slugOf = (id: string) => id.replace(/[^a-zA-Z0-9_]/g, "_");
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
    if (!f) return setPopup(null);
    if (f.layer.id === "coverage-fill") {
      // A footprint: popup its title + a link to open the item (don't yank the user off the map).
      setPopup({ lng: e.lngLat.lng, lat: e.lngLat.lat, title: String(f.properties?.title ?? ""),
                 props: {}, href: f.properties?.href ? String(f.properties.href) : undefined });
      return;
    }
    const l = layerByMapId[f.layer.id];
    setPopup({ lng: e.lngLat.lng, lat: e.lngLat.lat, title: l?.title ?? "", props: f.properties ?? {} });
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
      <Geocoder onPick={(b) => mapRef.current?.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: 40, maxZoom: 14, duration: 800 })} />
      <div className="absolute right-2 top-2 z-10 flex gap-1 text-xs">
        <UiSegmented value={basemap} onValueChange={setBasemap} items={BASEMAP_ITEMS}
          className="bg-card/95 shadow" />
        {footprints.length > 0 && (
          <Toggle pressed={showCoverage} onPressedChange={setShowCoverage}
            title="Show every item's footprint (what's mapped where)"
            className="cursor-pointer select-none rounded-md border border-input bg-card/95 px-2 py-1 text-foreground shadow hover:bg-muted data-[pressed]:bg-primary data-[pressed]:text-primary-foreground">
            Coverage · {footprints.length}
          </Toggle>
        )}
      </div>

      {/* Scale-gated overlays: name the layers this zoom hides, and offer the one move that reveals
          them all. */}
      {hidden && (
        <ZoomGateNotice gate={hidden.gate} dir={hiddenDir} subject={hidden.subject}
          onZoom={() => easeZoomTo(gateZoom(hidden.gate, hiddenDir))} />
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

      {layers.map((l, i) => {
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
        const c = colorFor(i);  // color rotates by position — fine to stay index-based
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
              // Explicit `source` — react-map-gl doesn't inject it for Layers inside a Fragment,
              // so without it maplibre throws "missing required property source".
              <>
                <Layer id={`pm-${s}-fill`} source={`pm-${s}`} type="fill" source-layer={pmLayer} paint={{ "fill-color": c, "fill-opacity": 0.15 }} />
                <Layer id={`pm-${s}-line`} source={`pm-${s}`} type="line" source-layer={pmLayer} paint={{ "line-color": c, "line-width": 1.2 }} />
                <Layer id={`pm-${s}-circle`} source={`pm-${s}`} type="circle" source-layer={pmLayer} paint={{ "circle-color": c, "circle-radius": 3, "circle-opacity": 0.85 }} />
              </>
            )}
          </Source>
        );
      })}

      {popup && (
        <Popup longitude={popup.lng} latitude={popup.lat} onClose={() => setPopup(null)} closeButton maxWidth="320px">
          {popup.title && <div className="mb-1 text-xs font-semibold text-gray-900">{popup.title}</div>}
          {popup.href ? (
            <button onClick={() => { onPickFootprint?.(popup.href!); setPopup(null); }}
              className="text-xs font-medium text-primary underline underline-offset-2 hover:opacity-80">
              Open item →
            </button>
          ) : (
            <FeatureProps props={popup.props} />
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
    <form onSubmit={search} className="absolute left-2 top-2 z-10 flex items-center gap-1 rounded-md border border-border bg-card/95 p-1 text-xs shadow">
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
  const rows = Object.entries(props).filter(([, v]) => v !== null && v !== "").slice(0, 14);
  if (!rows.length) return <em className="text-muted-foreground">No attributes.</em>;
  return (
    <table className="border-collapse text-xs">
      <tbody>
        {rows.map(([k, v]) => (
          <tr key={k}>
            <td className="pr-2 align-top font-medium text-gray-600">{k}</td>
            <td className="align-top text-gray-900">{String(v)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
