// ONE persistent maplibre map for the whole item-detail preview. It is mounted once by
// PreviewMapProvider (in App, above the list/item boundary) so its WebGL context is created exactly
// once and NEVER torn down. Each preview renders a lightweight <PreviewMapSlot> that (a) publishes a
// spec describing what to draw and (b) registers a target <div>; the map's DOM is React-portaled into
// that target, so navigating items just swaps sources on the same map — no mount/unmount, no context
// churn, no WebGL-context-exhaustion freeze. Consolidates the old PmtilesMap / CogMap /
// RasterMosaicPreview / FootprintMini into one component.
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import maplibregl from "maplibre-gl";
import { Layer, type LayerProps, Map as MapGL, type MapLayerMouseEvent, type MapRef, NavigationControl, Popup, Source } from "react-map-gl/maplibre";
import { ensureCogProtocol } from "./cog";
import { Legend } from "./legend";
import { CommentsPanel } from "./CommentsPanel";
import { boundsOf, type FocusSel, type MapPick, nextPick, validBbox } from "./map-model";
import { classificationEntries, defaultStyleUrl, IS_REVIEW, primaryKeyOf, rendersOf, type StacDoc, useLiveLegend, useStyleLayers } from "./stac";
import { gateOf, gateZoom, useGateDir, ZoomGateNotice } from "./zoomgate";

const POSITRON = "https://tiles.openfreemap.org/styles/positron";

const bboxPolygon = (b: number[]): GeoJSON.Polygon => {
  const [w, s, e, n] = b;
  return { type: "Polygon", coordinates: [[[w, s], [e, s], [e, n], [w, n], [w, s]]] };
};

// Neutral, geometry-agnostic render used until a ugs-styles style is bound — visible borders, not
// faux cartography. fill/line/circle all added so any geometry type shows.
const NEUTRAL_LAYERS = [
  { type: "fill", filter: ["==", ["geometry-type"], "Polygon"],
    paint: { "fill-color": "#6b7280", "fill-opacity": 0.15, "fill-outline-color": "#374151" } },
  { type: "line", filter: ["match", ["geometry-type"], ["LineString", "Polygon"], true, false],
    paint: { "line-color": "#374151", "line-width": 1.1 } },
  { type: "circle", filter: ["==", ["geometry-type"], "Point"],
    paint: { "circle-color": "#374151", "circle-radius": 3.5, "circle-opacity": 0.85 } },
];

// Load a baked sprite sheet (pie-wedge icons for box-type) into the map via addImage. Idempotent.
async function loadSpriteImages(map: maplibregl.Map, base: string): Promise<void> {
  const hi = (window.devicePixelRatio || 1) >= 1.5 ? "@2x" : "";
  const [index, blob] = await Promise.all([
    fetch(`${base}${hi}.json`).then((r) => r.json()),
    fetch(`${base}${hi}.png`).then((r) => r.blob()),
  ]);
  const sheet = await createImageBitmap(blob);
  try {
    for (const [name, f] of Object.entries(index as Record<string, { x: number; y: number; width: number; height: number; pixelRatio: number }>)) {
      if (map.hasImage(name)) continue;
      const img = await createImageBitmap(sheet, f.x, f.y, f.width, f.height);
      map.addImage(name, img, { pixelRatio: f.pixelRatio });
      img.close();
    }
  } finally {
    sheet.close();
  }
}

// What the persistent map should draw. Built per-preview at the call site (item-level vector/raster,
// asset-level cog, fallback footprint). null = nothing to show (list view / aspatial / non-map tab).
export type PreviewSpec =
  | { kind: "vector"; item: StacDoc; pmHref: string; sourceLayer: string }
  | { kind: "cog"; item: StacDoc; href: string }
  | { kind: "rasterpm"; item: StacDoc; href: string }
  | { kind: "footprint"; item: StacDoc; geometry: GeoJSON.Geometry }
  | null;

const specItemId = (s: PreviewSpec): string => (s ? String(s.item.id ?? "") : "");

// Footprint spec for an item with no previewable file: draw its geometry, else its (valid) bbox
// outline, else nothing.
export function footprintSpecOf(item: StacDoc): PreviewSpec {
  const b = validBbox(item.bbox);
  const geometry = (item.geometry as GeoJSON.Geometry | null | undefined) ?? (b ? bboxPolygon(b) : null);
  return geometry ? { kind: "footprint", item, geometry } : null;
}

// ---- provider / context: holds the cross-boundary state the portaled map shares with the table ----
type Ctx = {
  setSpec: (s: PreviewSpec) => void;
  registerSlot: (el: HTMLElement | null) => void;
  focus: FocusSel | null;
  setFocus: (f: FocusSel | null) => void;
  pick: MapPick | null;
  onFeatureClick: (id: number) => void;
  // Which `ugs:renders` entry the "Symbolize by" picker is on. Published so the endpoints panel can
  // hand out the style/ArcGIS URL for the symbology you are actually looking at, rather than always
  // the first one alphabetically.
  render: string;
};
const PreviewMapCtx = createContext<Ctx | null>(null);

export function usePreviewMap(): Ctx {
  const c = useContext(PreviewMapCtx);
  if (!c) throw new Error("usePreviewMap must be used within <PreviewMapProvider>");
  return c;
}

export function PreviewMapProvider({ children }: { children: React.ReactNode }) {
  const [spec, setSpec] = useState<PreviewSpec>(null);
  const [slotEl, setSlotEl] = useState<HTMLElement | null>(null);
  const [focus, setFocus] = useState<FocusSel | null>(null);
  const [pick, setPick] = useState<MapPick | null>(null);
  // Mirrors the map's "Symbolize by" picker. Owned here, set by the map, so consumers outside the
  // map subtree (the endpoints panel) can see which symbology is on screen.
  const [render, setRender] = useState("");

  // Reset the cross-boundary wires when the shown item changes (a stale fly/highlight would mislead).
  const itemId = specItemId(spec);
  useEffect(() => { setFocus(null); setPick(null); }, [itemId]);

  const registerSlot = useCallback((el: HTMLElement | null) => setSlotEl(el), []);
  const onFeatureClick = useCallback((id: number) => setPick((p) => nextPick(p, id)), []);

  const ctx = useMemo<Ctx>(
    () => ({ setSpec, registerSlot, focus, setFocus, pick, onFeatureClick, render }),
    [focus, pick, registerSlot, onFeatureClick, render],
  );

  return (
    <PreviewMapCtx.Provider value={ctx}>
      {children}
      <PreviewMap spec={spec} slotEl={slotEl} focus={focus} onFeatureClick={onFeatureClick}
        onRenderChange={setRender} />
    </PreviewMapCtx.Provider>
  );
}

// ---- the single persistent map, portaled into the active slot (or a hidden keep-alive holder) ----
function PreviewMap({ spec, slotEl, focus, onFeatureClick, onRenderChange }: {
  spec: PreviewSpec; slotEl: HTMLElement | null;
  focus: FocusSel | null; onFeatureClick: (id: number) => void;
  onRenderChange: (r: string) => void;
}) {
  const mapRef = useRef<MapRef>(null);
  // The map is portaled into ONE stable, detached container that NEVER changes identity, so the
  // <MapGL> subtree (and its WebGL context) is created once and never torn down. We then move that
  // container element between the active slot and a hidden parking holder with plain appendChild —
  // an imperative DOM move that maplibre survives (React never sees the parent change, so it can't
  // remount). Portaling into the slot directly would remount on every nav (React recreates a portal's
  // subtree when its container prop changes) — the very churn we're removing.
  const [container] = useState(() => document.createElement("div"));
  const holderRef = useRef<HTMLDivElement>(null);
  const [mapLoaded, setMapLoaded] = useState(false);
  const [showDem, setShowDem] = useState(false);
  const [cogReady, setCogReady] = useState(false);

  const isVector = spec?.kind === "vector";
  const item = spec?.item;
  const itemId = specItemId(spec);

  // ---- vector-specific state (reset per item) ----
  const renders = useMemo(() => (isVector && item ? rendersOf(item) : {}), [isVector, item]);
  const renderKeys = Object.keys(renders);
  const [sel, setSel] = useState<string>("");
  useEffect(() => { setSel(renders.default ? "default" : Object.keys(renders)[0] ?? ""); }, [itemId]);  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { onRenderChange(sel); }, [sel, onRenderChange]);
  const active = renders[sel];
  const styleUrl = isVector && item ? (active?.style_url ?? defaultStyleUrl(item)) : undefined;
  const sprite = active?.sprite;
  const styleLayers = useStyleLayers(styleUrl);  // null while loading/error → NEUTRAL_LAYERS
  // Icon renders bake their colours into the sprite, so the legend can't be derived from the paint.
  // Read it live from the ugs-styles manifest; the item's own `legend` is a bind-time snapshot that
  // goes stale as soon as a style publishes, so it's only the fallback.
  const liveLegend = useLiveLegend(styleUrl, item ? String(item.id ?? "") : undefined, sel);
  const [spriteReady, setSpriteReady] = useState(false);
  const [popup, setPopup] = useState<{ lng: number; lat: number; props: Record<string, unknown>; fid: number | null } | null>(null);
  const [reviewFeature, setReviewFeature] = useState<{ pkVal: string; props: Record<string, unknown> } | null>(null);
  const pkCol = isVector && item ? primaryKeyOf(item) : "";
  useEffect(() => { setPopup(null); setReviewFeature(null); }, [itemId]);

  // Preload the render's sprite (icon renders) before its symbol layers mount.
  useEffect(() => {
    if (!sprite) { setSpriteReady(true); return; }
    setSpriteReady(false);
    const map = mapRef.current?.getMap();
    if (!map || !mapLoaded) return;
    let live = true;
    loadSpriteImages(map, sprite).then(() => { if (live) setSpriteReady(true); }).catch(() => { if (live) setSpriteReady(true); });
    return () => { live = false; };
  }, [sprite, mapLoaded]);

  // Register the cog:// protocol lazily the first time a COG spec appears.
  useEffect(() => {
    if (spec?.kind !== "cog" || cogReady) return;
    let live = true;
    ensureCogProtocol().then(() => { if (live) setCogReady(true); });
    return () => { live = false; };
  }, [spec?.kind, cogReady]);

  // ---- imperative camera fit: a persistent map honours initialViewState only once, so fit on every
  // spec change (keyed on item+kind). COG has no STAC bbox for many items → fit to its GeoTIFF extent.
  const fitKey = spec ? `${itemId}|${spec.kind}` : "";
  const lastFit = useRef<string>("");
  useEffect(() => {
    const map = mapRef.current?.getMap();
    if (!spec || !map || !mapLoaded || fitKey === lastFit.current) return;
    lastFit.current = fitKey;
    setShowDem(false);  // terrain resets per item
    if (spec.kind === "cog") {
      let live = true;
      (async () => {
        await ensureCogProtocol();
        try {
          const { getCogMetadata } = await import("@geomatico/maplibre-cog-protocol");
          const meta = await getCogMetadata(spec.href);
          const b = validBbox(meta?.bbox as number[] | undefined) ?? validBbox(spec.item.bbox);
          if (live && b) map.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: 16, duration: 0 });
        } catch { /* keep default view */ }
      })();
      return () => { live = false; };
    }
    const b = boundsOf(spec.item);
    if (b) map.fitBounds(b, { padding: 16, duration: 0 });
  }, [fitKey, mapLoaded, spec]);

  // Fly to a picked feature (table row click). Keyed on focus.key so re-picking the same row re-flies.
  const fb = focus?.bbox;
  const focusKey = focus?.key;
  useEffect(() => {
    if (!fb || !mapRef.current) return;
    mapRef.current.fitBounds([[fb[0], fb[1]], [fb[2], fb[3]]], { padding: 60, maxZoom: 14, duration: 800 });
  }, [focusKey]);  // eslint-disable-line react-hooks/exhaustive-deps

  // Move the stable container into the active slot (or the hidden holder when none), then resize —
  // the map just landed in a differently-sized box.
  useEffect(() => {
    const dest = slotEl ?? holderRef.current;
    if (dest && container.parentNode !== dest) dest.appendChild(container);
    const t = setTimeout(() => mapRef.current?.getMap()?.resize(), 0);
    return () => clearTimeout(t);
  }, [slotEl, container]);

  // ---- derived vector render data ----
  const layers = isVector ? (sprite && !spriteReady ? [] : (styleLayers ?? NEUTRAL_LAYERS)) : [];
  const layerIds = layers.map((l, i) => `pm-${sel}-${(l as { id?: string }).id ?? i}`);
  // The camera fits the item's bbox with no maxZoom, so a statewide topic lands near z6 — below the
  // scale gate a render may carry (PLSS sections: z≥11.13). Read the gate off the layers actually
  // mounted, so NEUTRAL_LAYERS (ungated) can't make a loading style look hidden.
  const gate = gateOf(layers);
  const gateDirection = useGateDir(mapRef, gate, mapLoaded);
  const hlGeom: GeoJSON.Geometry | null = isVector ? (focus?.geometry ?? (fb ? bboxPolygon(fb) : null)) : null;

  const onMapClick = (e: MapLayerMouseEvent) => {
    if (!isVector) return;
    const f = e.features?.[0];
    if (!f) { setPopup(null); return; }
    const props = (f.properties ?? {}) as Record<string, unknown>;
    const fid = f.id != null ? Number(f.id) : null;
    setPopup({ lng: e.lngLat.lng, lat: e.lngLat.lat, props, fid });
    if (fid != null) onFeatureClick(fid);
  };

  const cluster = (
    <>
      {isVector && renderKeys.length > 1 && (
        <div className="mb-1.5 flex items-center gap-2 text-xs">
          <span className="text-muted-foreground">Symbolize by</span>
          <select value={sel} onChange={(e) => setSel(e.target.value)}
            className="rounded border border-input bg-card px-2 py-1 text-foreground">
            {renderKeys.map((k) => <option key={k} value={k}>{renders[k].title ?? k}</option>)}
          </select>
        </div>
      )}
      <div className="mt-2 h-96 w-full overflow-hidden rounded-md border border-border bg-muted">
        <MapGL
          ref={mapRef}
          mapLib={maplibregl}
          onLoad={() => setMapLoaded(true)}
          initialViewState={{ longitude: -111.7, latitude: 39.3, zoom: 6 }}
          mapStyle={POSITRON}
          interactiveLayerIds={isVector ? layerIds : undefined}
          onClick={onMapClick}
          style={{ width: "100%", height: "100%" }}
          maxPitch={85}
          terrain={showDem ? { source: "terrain-rgb-source", exaggeration: 1.5 } : undefined}
        >
          <NavigationControl position="top-right" showCompass={false} />

          <div className="absolute top-2.5 right-12 z-10">
            <button
              onClick={() => {
                const next = !showDem;
                setShowDem(next);
                mapRef.current?.getMap().easeTo({ pitch: next ? 48 : 0, duration: 500 });
              }}
              className={`flex items-center gap-1 px-2.5 py-1 text-xs font-semibold rounded-md shadow-sm border transition ${
                showDem ? "bg-primary text-primary-foreground border-primary"
                  : "bg-card/90 backdrop-blur-sm text-foreground border-border hover:bg-muted"}`}
              title="Toggle 3D Topography"
            >
              <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 20l-5.447-2.724A1 1 0 013 16.382V5.618a1 1 0 011.447-.894L9 7m0 13l6-3m-6 3V7m6 10l4.553 2.276A1 1 0 0021 18.382V7.618a1 1 0 00-.553-.894L15 4m0 13V4m0 0L9 7" />
              </svg>
              <span>3D Terrain</span>
            </button>
          </div>

          {gate && gateDirection && (
            <ZoomGateNotice gate={gate} dir={gateDirection}
              onZoom={() => mapRef.current?.getMap().easeTo({ zoom: gateZoom(gate, gateDirection), duration: 600 })} />
          )}

          {showDem && (
            <Source id="terrain-rgb-source" type="raster-dem"
              tiles={["https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"]}
              encoding="terrarium" tileSize={256} />
          )}

          {/* Vector PMTiles */}
          {isVector && spec && (
            <Source id="pm-prev" type="vector" url={`pmtiles://${spec.pmHref}`}>
              {layers.map((l, i) => (
                <Layer key={layerIds[i]} {...({ ...l, id: layerIds[i], source: "pm-prev", "source-layer": spec.sourceLayer } as unknown as LayerProps)} />
              ))}
            </Source>
          )}
          {isVector && hlGeom && (
            <Source id="pm-hl" type="geojson" data={{ type: "Feature", properties: {}, geometry: hlGeom }}>
              <Layer id="pm-hl-fill" type="fill" paint={{ "fill-color": "#f59e0b", "fill-opacity": 0.25 }} />
              <Layer id="pm-hl-line" type="line" paint={{ "line-color": "#f59e0b", "line-width": 3 }} />
              <Layer id="pm-hl-pt" type="circle" paint={{ "circle-radius": 7, "circle-color": "#f59e0b", "circle-stroke-color": "#fff", "circle-stroke-width": 2 }} />
            </Source>
          )}

          {/* Raster PMTiles mosaic */}
          {spec?.kind === "rasterpm" && (
            <Source id="raster-mosaic" type="raster" url={`pmtiles://${spec.href}`} tileSize={256}>
              <Layer id="raster-mosaic-layer" type="raster" />
            </Source>
          )}

          {/* COG raster (once the cog:// protocol is registered) */}
          {spec?.kind === "cog" && cogReady && (
            <Source id="cog" type="raster" url={`cog://${spec.href}`} tileSize={256}>
              <Layer id="cog-raster" type="raster" />
            </Source>
          )}

          {/* Footprint outline */}
          {spec?.kind === "footprint" && (
            <Source id="fp-mini" type="geojson" data={{ type: "Feature", properties: {}, geometry: spec.geometry }}>
              <Layer id="fp-mini-line" type="line" paint={{ "line-color": "#888", "line-width": 1.5 }} />
            </Source>
          )}

          {isVector && popup && (
            <Popup longitude={popup.lng} latitude={popup.lat} onClose={() => setPopup(null)} closeButton maxWidth="320px">
              <div className="max-h-56 overflow-auto">
                <table className="border-collapse text-[11px]">
                  <tbody>
                    {Object.entries(popup.props).filter(([, v]) => v !== null && v !== "").map(([k, v]) => (
                      <tr key={k}>
                        <td className="whitespace-nowrap py-0.5 pr-2 align-top text-gray-500">{k}</td>
                        <td className="py-0.5 text-gray-900">{String(v)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {IS_REVIEW && popup.props[pkCol] != null && (
                  <button
                    className="mt-1.5 rounded border border-amber-500/50 bg-amber-500/10 px-2 py-1 text-[11px] font-medium text-amber-700 hover:bg-amber-500/20"
                    onClick={() => { setReviewFeature({ pkVal: String(popup.props[pkCol]), props: popup.props }); setPopup(null); }}>
                    💬 Comment on this feature
                  </button>
                )}
              </div>
            </Popup>
          )}
        </MapGL>
      </div>

      {isVector && IS_REVIEW && reviewFeature && item && (
        <div className="mt-2 rounded-md border border-amber-500/40 bg-amber-500/[0.04] p-3">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold">Feature review</h3>
            <button className="text-xs text-muted-foreground hover:underline" onClick={() => setReviewFeature(null)}>close</button>
          </div>
          <CommentsPanel itemId={String(item.id ?? "")} target={{ kind: "row", rowKey: pkCol, rowVal: reviewFeature.pkVal }}
            label={`Comments on ${pkCol} ${reviewFeature.pkVal}`} />
        </div>
      )}

      {isVector && item && (
        <Legend layers={styleLayers ?? undefined}
          entries={liveLegend?.entries ?? active?.legend ?? classificationEntries(item)}
          title={liveLegend?.field ?? (active?.legend ? "box type" : undefined)}
          name={String(item.properties?.title ?? item.id)} />
      )}
    </>
  );

  return (
    <>
      {/* Hidden keep-alive holder: the map parks here whenever no slot is active (context stays alive). */}
      <div ref={holderRef} hidden aria-hidden />
      {/* Portal into the STABLE container (moved between holder/slot imperatively above). */}
      {createPortal(cluster, container)}
    </>
  );
}

// Per-preview placeholder: renders the box the map is portaled into, and publishes the spec. This is
// the ONLY thing that mounts/unmounts per item — a cheap DOM node, no WebGL.
export function PreviewMapSlot({ spec }: { spec: PreviewSpec }) {
  const { setSpec, registerSlot } = usePreviewMap();
  const elRef = useRef<HTMLDivElement>(null);

  // Publish spec on change. Kept in an effect so render stays pure.
  useEffect(() => { setSpec(spec); }, [spec, setSpec]);
  // Register/clear this slot as the portal target across mount/unmount.
  useEffect(() => {
    registerSlot(elRef.current);
    return () => { registerSlot(null); setSpec(null); };
  }, [registerSlot, setSpec]);

  return <div ref={elRef} />;
}
