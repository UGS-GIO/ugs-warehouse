// The maplibre half of the preview map, loaded on demand — the catalog, search and doc views never
// draw one. Mounted once by PreviewMapProvider and NEVER torn down: its DOM is portaled into
// whichever slot is active, so navigating items swaps sources on one live WebGL context.
import { useEffect, useRef, useState } from "react";
import { sameFeature } from "@/lib/same-feature";
import { createPortal } from "react-dom";
import maplibregl from "@/map/maplibre-lib";
import type { TerrainSpecification } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { MapControl } from "./map-control";
import { GeolocateControl, Layer, type LayerProps, Map as MapGL, type MapLayerMouseEvent, type MapRef, NavigationControl, Source } from "react-map-gl/maplibre";
import { ensureCogProtocol } from "./cog";
import { DIRECT, protomapsStyle } from "./basemap-style";
import { ensurePmtilesProtocol } from "./pmtiles-protocol";
import { Legend } from "./legend";
import { boundsOf, type FocusSel, GEOM_FILTER, validBbox } from "./map-model";
import { classificationEntries, defaultStyleUrl, useLiveLegend, useStyleLayers } from "@/stac";
import { type PreviewSpec, type Renders, specItemId } from "./preview-spec";
import { gateOf, gateZoom, useGateDir, ZoomGateNotice } from "./zoomgate";
import { UiSelect } from "@/ui/select";

ensurePmtilesProtocol();   // this module is lazy, so registration happens the first time a map loads

const LIGHT_BASEMAP = protomapsStyle("white", DIRECT);

// Neutral, geometry-agnostic render used until a ugs-styles style is bound — visible borders, not
// faux cartography. fill/line/circle all added so any geometry type shows.
const NEUTRAL_LAYERS = [
  { type: "fill", filter: GEOM_FILTER.fill,
    paint: { "fill-color": "#6b7280", "fill-opacity": 0.15, "fill-outline-color": "#374151" } },
  { type: "line", filter: GEOM_FILTER.line,
    paint: { "line-color": "#374151", "line-width": 1.1 } },
  { type: "circle", filter: GEOM_FILTER.point,
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

// ---- the single persistent map, portaled into the active slot (or a hidden keep-alive holder) ----
export default function PreviewMap({ spec, slotEl, focus, onFeatureClick, onMismatch, renders, sel, onSel, onFeatureSelect, onClearSelection }: {
  spec: PreviewSpec; slotEl: HTMLElement | null;
  focus: FocusSel | null; onFeatureClick: (id: number, props?: Record<string, unknown>) => void;
  onMismatch?: () => void;
  renders: Renders; sel: string; onSel: (r: string) => void;
  onFeatureSelect?: (props: Record<string, unknown>, fid: number | null) => void;
  onClearSelection?: () => void;
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

  // ---- vector-specific state (`renders`/`sel` are owned by the provider) ----
  const renderKeys = Object.keys(renders);
  const active = renders[sel];
  const styleUrl = isVector && item ? (active?.style_url ?? defaultStyleUrl(item)) : undefined;
  const sprite = active?.sprite;
  const styleLayers = useStyleLayers(styleUrl);  // null while loading/error → NEUTRAL_LAYERS
  // Icon renders bake their colours into the sprite, so the legend can't be derived from the paint.
  // Read it live from the ugs-styles manifest; the item's own `legend` is a bind-time snapshot that
  // goes stale as soon as a style publishes, so it's only the fallback.
  const liveLegend = useLiveLegend(styleUrl, item ? String(item.id ?? "") : undefined, sel);
  const [spriteReady, setSpriteReady] = useState(false);

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
    setShowDem(false);  // terrain and tilt reset per item
    map.setPitch(0);
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
  // The picked/clicked feature is outlined by setting feature-state `hl` on its PMTiles tile feature
  // (keyed by feature_id), NOT by reading geometry from the parquet, which pulls the whole geom
  // column and OOMs the tab on large layers. One writer (highlightFeature) so only ever one feature
  // is lit, and it survives the fly: feature-state applies to the feature whenever its tile loads, so
  // setting it before the camera arrives is fine. Tracks the sourceLayer it lit under, so the clear
  // targets the right tile even across an item switch. (ALL-6001)
  const sourceLayer = spec?.kind === "vector" ? spec.sourceLayer : undefined;
  const hlRef = useRef<{ id: number; sourceLayer: string } | null>(null);
  const highlightFeature = (fid: number | null) => {
    const map = mapRef.current?.getMap();
    // No pm-prev source (non-vector item, or not added yet) means nothing to light or clear; any
    // prior state died with the source it was set on. Guarding on the source keeps setFeatureState
    // from throwing on the one expected case, so a genuinely unexpected error still surfaces.
    if (!map || !map.getSource("pm-prev")) { hlRef.current = null; return; }
    const prev = hlRef.current;
    // Only clear within the same source-layer. A cross-item change swaps sourceLayer, but that also
    // remounts the Source (key={itemId}), which drops its feature-state — so clearing the old layer
    // here is redundant, and on the new tiles (which lack it) it is at best a no-op.
    if (prev && prev.sourceLayer === sourceLayer && prev.id !== fid) {
      map.setFeatureState({ source: "pm-prev", sourceLayer, id: prev.id }, { hl: false });
    }
    if (fid != null && sourceLayer) {
      map.setFeatureState({ source: "pm-prev", sourceLayer, id: fid }, { hl: true });
      hlRef.current = { id: fid, sourceLayer };
    } else {
      hlRef.current = null;
    }
  };
  // Table row-click → outline focus.featureId. Keyed on focus.key so re-picking the same row re-lights
  // it; on itemId (source swap) so a stale id can't light a same-id feature in the next dataset; on
  // mapLoaded so a focus set before the map is ready applies once it is. Map clicks call
  // highlightFeature directly (below) without touching focus, so they don't retrigger this.
  // Once the tiles are in, the lit feature must be the row's record: a map and table from
  // different ingests number rows differently (lib/same-feature). A mismatch is left unlit.
  useEffect(() => {
    const fid = focus?.featureId ?? null;
    highlightFeature(fid);
    const map = mapRef.current?.getMap();
    const props = focus?.props;
    if (!map || fid == null || !props || !sourceLayer) return;
    const check = () => {
      const [f] = map.querySourceFeatures("pm-prev", { sourceLayer, filter: ["==", ["id"], fid] });
      if (f && !sameFeature(f.properties ?? {}, props)) { highlightFeature(null); onMismatch?.(); }
    };
    map.once("idle", check);
    return () => { map.off("idle", check); };
  },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [itemId, focusKey, mapLoaded]);

  const onMapClick = (e: MapLayerMouseEvent) => {
    if (!isVector) return;
    const f = e.features?.[0];
    if (!f) { highlightFeature(null); onClearSelection?.(); return; }
    const props = (f.properties ?? {}) as Record<string, unknown>;
    const fid = f.id != null ? Number(f.id) : null;
    highlightFeature(fid);  // exact outline via the tile's feature-state, no parquet read
    onFeatureSelect?.(props, fid);
    if (fid != null) onFeatureClick(fid, props);
  };

  const cluster = (
    <>
      {isVector && renderKeys.length > 1 && (
        <div className="mb-1.5 flex items-center gap-2 text-xs">
          <span className="text-muted-foreground">Symbolize by</span>
          <UiSelect value={sel} onValueChange={onSel}
            items={renderKeys.map((k) => ({ value: k, label: String(renders[k].title ?? k) }))} />
        </div>
      )}
      <div className="mt-2 h-96 w-full overflow-hidden rounded-md border border-border bg-muted">
        <MapGL
          ref={mapRef}
          mapLib={maplibregl}
          onLoad={() => setMapLoaded(true)}
          initialViewState={{ longitude: -111.7, latitude: 39.3, zoom: 6 }}
          mapStyle={LIGHT_BASEMAP}
          interactiveLayerIds={isVector ? layerIds : undefined}
          onClick={onMapClick}
          style={{ width: "100%", height: "100%" }}
          maxPitch={85}
          // null, not undefined: react-map-gl skips an undefined terrain, so it never turned off.
          terrain={showDem ? { source: "terrain-rgb-source", exaggeration: 1.5 } : null as unknown as TerrainSpecification}
        >
          <MapControl position="top-right">
            <button
              onClick={() => {
                const next = !showDem;
                setShowDem(next);
                mapRef.current?.getMap().easeTo({ pitch: next ? 48 : 0, duration: 500 });
              }}
              className={`flex items-center gap-1 px-2.5 py-1 text-xs font-semibold rounded-md shadow-sm border transition ${
                showDem ? "bg-primary text-primary-foreground border-primary"
                  : "bg-card/90 backdrop-blur-sm text-foreground border-border hover:bg-hover"}`}
              title="Toggle 3D Topography"
            >
              <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 20l-5.447-2.724A1 1 0 013 16.382V5.618a1 1 0 011.447-.894L9 7m0 13l6-3m-6 3V7m6 10l4.553 2.276A1 1 0 0021 18.382V7.618a1 1 0 00-.553-.894L15 4m0 13V4m0 0L9 7" />
              </svg>
              <span>3D Terrain</span>
            </button>
          </MapControl>

          <NavigationControl position="top-right" showCompass={false} />
          <GeolocateControl position="top-right" trackUserLocation
            positionOptions={{ enableHighAccuracy: true }} />

          {gate && gateDirection && (
            <MapControl position="bottom-left">
              <ZoomGateNotice gate={gate} dir={gateDirection}
                onZoom={() => mapRef.current?.getMap().easeTo({ zoom: gateZoom(gate, gateDirection), duration: 600 })} />
            </MapControl>
          )}

          {showDem && (
            <Source id="terrain-rgb-source" type="raster-dem"
              tiles={["https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"]}
              encoding="terrarium" tileSize={256} />
          )}

          {/* Vector PMTiles + the highlight layers over it (same source/source-layer). The picked or
              clicked feature is outlined straight from the tile via feature-state, so no geometry is
              read from the parquet. Each paint uses ['feature-state','hl'] so only the lit feature
              draws; the geometry filters keep the circle off polygon/line vertices, and the layers
              sit after the styled ones so the outline draws on top. (ALL-6001)
              Keyed by itemId so a vector→vector nav rebuilds the source and its layers with the new
              item's source-layer (react-map-gl updates a url in place but never re-points an existing
              layer's source-layer), and each item starts with clean feature-state. */}
          {isVector && spec && (
            <Source key={itemId} id="pm-prev" type="vector" url={`pmtiles://${spec.pmHref}`}>
              {layers.map((l, i) => (
                <Layer key={layerIds[i]} {...({ ...l, id: layerIds[i], source: "pm-prev", "source-layer": spec.sourceLayer } as unknown as LayerProps)} />
              ))}
              <Layer {...({ id: "pm-hl-fill", type: "fill", source: "pm-prev", "source-layer": spec.sourceLayer, filter: GEOM_FILTER.fill,
                paint: { "fill-color": "#f59e0b", "fill-opacity": ["case", ["boolean", ["feature-state", "hl"], false], 0.3, 0] } } as unknown as LayerProps)} />
              <Layer {...({ id: "pm-hl-line", type: "line", source: "pm-prev", "source-layer": spec.sourceLayer, filter: GEOM_FILTER.line,
                paint: { "line-color": "#f59e0b", "line-width": ["case", ["boolean", ["feature-state", "hl"], false], 3, 0] } } as unknown as LayerProps)} />
              <Layer {...({ id: "pm-hl-pt", type: "circle", source: "pm-prev", "source-layer": spec.sourceLayer, filter: GEOM_FILTER.point,
                paint: { "circle-color": "#f59e0b", "circle-stroke-color": "#fff",
                  "circle-radius": ["case", ["boolean", ["feature-state", "hl"], false], 7, 0],
                  "circle-stroke-width": ["case", ["boolean", ["feature-state", "hl"], false], 2, 0] } } as unknown as LayerProps)} />
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
        </MapGL>
      </div>

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
