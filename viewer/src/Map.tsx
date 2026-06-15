import maplibregl from "maplibre-gl";
import { useEffect, useRef, useState } from "react";
import { Layer, type MapLayerMouseEvent, Map as MapGL, type MapRef, Popup, Source, type ViewStateChangeEvent } from "react-map-gl/maplibre";
import { type StacDoc } from "./stac";

// A topic toggled on in the map. Built by App from the active set × allItems.
export type ActiveLayer = { id: string; title: string; pmHref: string; pmLayer: string; bbox?: number[] };

// Distinct colors cycled per active layer.
export const LAYER_COLORS = ["#d1491c", "#2b6cdf", "#1a7f4b", "#9333ea", "#d97706", "#0891b2", "#be185d", "#65a30d"];
export const colorFor = (i: number) => LAYER_COLORS[i % LAYER_COLORS.length];

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
const SATELLITE: maplibregl.StyleSpecification = {
  version: 8,
  sources: { sat: { type: "raster", tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"], tileSize: 256, attribution: "Imagery © Esri" } },
  layers: [{ id: "sat", type: "raster", source: "sat" }],
};
const BASEMAPS: Record<string, string | maplibregl.StyleSpecification> = {
  Streets: ofm("liberty"), Light: ofm("positron"), Satellite: SATELLITE,
};

type PopupInfo = { lng: number; lat: number; title: string; props: Record<string, unknown> };

// Union of layer bboxes → [w,s,e,n], or null.
function unionBbox(layers: ActiveLayer[]): [number, number, number, number] | null {
  const bs = layers.map((l) => l.bbox).filter((b): b is number[] => Array.isArray(b) && b.length >= 4);
  if (!bs.length) return null;
  return [Math.min(...bs.map((b) => b[0])), Math.min(...bs.map((b) => b[1])),
          Math.max(...bs.map((b) => b[2])), Math.max(...bs.map((b) => b[3]))];
}

export function ItemMap({ item, layers }: { item?: StacDoc; layers: ActiveLayer[] }) {
  const mapRef = useRef<MapRef>(null);
  const [cursor, setCursor] = useState<"" | "pointer">("");
  const [popup, setPopup] = useState<PopupInfo | null>(null);
  const [basemap, setBasemap] = useState<keyof typeof BASEMAPS>("Streets");
  const initialCam = useRef(readCam());
  const lastFit = useRef<string | null>(null);
  const honorCam = useRef(Boolean(initialCam.current));

  // Fit to the union of active layers when the set changes (StrictMode-safe via lastFit;
  // a shared ?m= camera wins over the first auto-fit).
  const fitKey = layers.map((l) => l.id).join(",") || (item?.bbox?.join(",") ?? "");
  const fitBox = unionBbox(layers) ?? (item?.bbox?.slice(0, 4) as [number, number, number, number] | undefined);
  useEffect(() => {
    if (!fitKey || !fitBox || !mapRef.current || fitKey === lastFit.current) return;
    lastFit.current = fitKey;
    setPopup(null);
    if (honorCam.current) { honorCam.current = false; return; }
    const [w, s, e, n] = fitBox;
    mapRef.current.fitBounds([[w, s], [e, n]], { padding: 40, maxZoom: 12, duration: 600 });
  }, [fitKey]);

  const interactiveIds = layers.flatMap((_, i) => [`pm-${i}-fill`, `pm-${i}-line`, `pm-${i}-circle`]);

  const onClick = (e: MapLayerMouseEvent) => {
    const f = e.features?.[0];
    if (!f) return setPopup(null);
    const idx = Number(/^pm-(\d+)-/.exec(f.layer.id)?.[1] ?? -1);
    setPopup({ lng: e.lngLat.lng, lat: e.lngLat.lat, title: layers[idx]?.title ?? "", props: f.properties ?? {} });
  };

  return (
    <MapGL
      ref={mapRef}
      mapLib={maplibregl}
      initialViewState={initialCam.current ?? { longitude: -111.7, latitude: 39.3, zoom: 5.3 }}
      mapStyle={BASEMAPS[basemap]}
      style={{ width: "100%", height: "100%" }}
      interactiveLayerIds={interactiveIds}
      cursor={cursor}
      onMouseEnter={() => setCursor("pointer")}
      onMouseLeave={() => setCursor("")}
      onMoveEnd={(e: ViewStateChangeEvent) => writeCam(e.viewState)}
      onClick={onClick}
    >
      <Geocoder onPick={(b) => mapRef.current?.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: 40, maxZoom: 14, duration: 800 })} />
      <div className="absolute right-2 top-2 z-10 flex gap-1 rounded-md border border-border bg-card/95 p-1 text-xs shadow">
        {Object.keys(BASEMAPS).map((name) => (
          <button key={name} onClick={() => setBasemap(name)}
            className={`rounded px-2 py-0.5 ${basemap === name ? "bg-primary text-primary-foreground" : "text-foreground hover:bg-accent"}`}>
            {name}
          </button>
        ))}
      </div>

      {item?.geometry && (
        <Source id="footprint" type="geojson" data={{ type: "Feature", properties: {}, geometry: item.geometry }}>
          <Layer id="fp-line" type="line" paint={{ "line-color": "#888", "line-width": 1, "line-dasharray": [2, 2] }} />
        </Source>
      )}

      {layers.map((l, i) => {
        const c = colorFor(i);
        return (
          <Source key={l.id} id={`pm-${i}`} type="vector" url={`pmtiles://${l.pmHref}`}>
            <Layer id={`pm-${i}-fill`} type="fill" source-layer={l.pmLayer} paint={{ "fill-color": c, "fill-opacity": 0.15 }} />
            <Layer id={`pm-${i}-line`} type="line" source-layer={l.pmLayer} paint={{ "line-color": c, "line-width": 1.2 }} />
            <Layer id={`pm-${i}-circle`} type="circle" source-layer={l.pmLayer} paint={{ "circle-color": c, "circle-radius": 3, "circle-opacity": 0.85 }} />
          </Source>
        );
      })}

      {popup && (
        <Popup longitude={popup.lng} latitude={popup.lat} onClose={() => setPopup(null)} closeButton maxWidth="320px">
          {popup.title && <div className="mb-1 text-[12px] font-semibold text-gray-900">{popup.title}</div>}
          <FeatureProps props={popup.props} />
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
        className="w-40 rounded bg-transparent px-1.5 py-0.5 text-foreground placeholder:text-muted-foreground focus:outline-none" />
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
    <table className="border-collapse text-[12px]">
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
