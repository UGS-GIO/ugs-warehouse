import maplibregl from "maplibre-gl";
import { useEffect, useRef, useState } from "react";
import { Layer, type MapLayerMouseEvent, Map as MapGL, type MapRef, Popup, Source } from "react-map-gl/maplibre";
import { pmtilesLink, type StacDoc } from "./stac";

// PMTiles vector layers we render + identify against. fill/line/circle cover polygon/
// line/point — MapLibre simply draws nothing for non-matching geometry, so all three
// are safe to mount and make every feature clickable.
const PM_LAYERS = ["pm-fill", "pm-line", "pm-circle"];

// Keyless basemaps: OpenFreeMap vector styles + Esri World Imagery raster (no API key).
const ofm = (s: string) => `https://tiles.openfreemap.org/styles/${s}`;
const SATELLITE: maplibregl.StyleSpecification = {
  version: 8,
  sources: {
    sat: {
      type: "raster",
      tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"],
      tileSize: 256,
      attribution: "Imagery © Esri",
    },
  },
  layers: [{ id: "sat", type: "raster", source: "sat" }],
};
const BASEMAPS: Record<string, string | maplibregl.StyleSpecification> = {
  Streets: ofm("liberty"),
  Light: ofm("positron"),
  Satellite: SATELLITE,
};

type PopupInfo = { lng: number; lat: number; props: Record<string, unknown> };

// Footprint + PMTiles render are declarative; effects are camera-only (fitBounds) +
// cursor. onClick identifies the clicked feature.
export function ItemMap({ item }: { item?: StacDoc }) {
  const mapRef = useRef<MapRef>(null);
  const bbox = item?.bbox;
  const [cursor, setCursor] = useState<"" | "pointer">("");
  const [popup, setPopup] = useState<PopupInfo | null>(null);
  const [basemap, setBasemap] = useState<keyof typeof BASEMAPS>("Streets");

  useEffect(() => {
    if (bbox && mapRef.current) {
      const [w, s, e, n] = bbox;
      mapRef.current.fitBounds([[w, s], [e, n]], { padding: 40, maxZoom: 12, duration: 600 });
    }
    setPopup(null); // clear stale popup when switching items
  }, [bbox]);

  const pm = pmtilesLink(item);
  const pmLayer = pm?.["pmtiles:layers"]?.[0] ?? item?.id;

  const onClick = (e: MapLayerMouseEvent) => {
    const f = e.features?.[0];
    if (f) setPopup({ lng: e.lngLat.lng, lat: e.lngLat.lat, props: f.properties ?? {} });
    else setPopup(null);
  };

  return (
    <MapGL
      ref={mapRef}
      mapLib={maplibregl}
      initialViewState={{ longitude: -111.7, latitude: 39.3, zoom: 5.3 }}
      mapStyle={BASEMAPS[basemap]}
      style={{ width: "100%", height: "100%" }}
      interactiveLayerIds={pm && pmLayer ? PM_LAYERS : []}
      cursor={cursor}
      onMouseEnter={() => setCursor("pointer")}
      onMouseLeave={() => setCursor("")}
      onClick={onClick}
    >
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
          <Layer id="fp-fill" type="fill" paint={{ "fill-color": "#2b6cdf", "fill-opacity": 0.08 }} />
          <Layer id="fp-line" type="line" paint={{ "line-color": "#2b6cdf", "line-width": 1.5, "line-dasharray": [2, 1] }} />
        </Source>
      )}
      {pm && pmLayer && (
        <Source id="pm" type="vector" url={`pmtiles://${pm.href}`}>
          <Layer id="pm-fill" type="fill" source-layer={pmLayer} paint={{ "fill-color": "#d1491c", "fill-opacity": 0.15 }} />
          <Layer id="pm-line" type="line" source-layer={pmLayer} paint={{ "line-color": "#d1491c", "line-width": 1.2 }} />
          <Layer id="pm-circle" type="circle" source-layer={pmLayer} paint={{ "circle-color": "#d1491c", "circle-radius": 3, "circle-opacity": 0.8 }} />
        </Source>
      )}
      {popup && (
        <Popup longitude={popup.lng} latitude={popup.lat} onClose={() => setPopup(null)} closeButton maxWidth="320px">
          <FeatureProps props={popup.props} />
        </Popup>
      )}
    </MapGL>
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
