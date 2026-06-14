import maplibregl from "maplibre-gl";
import { useEffect, useRef } from "react";
import { Layer, Map as MapGL, type MapRef, Source } from "react-map-gl/maplibre";
import { pmtilesLink, type StacDoc } from "./stac";

// Footprint + PMTiles render are declarative (Source/Layer JSX). The one effect is
// camera-only — fitBounds when the selected item's bbox changes (the accepted
// react-map-gl pattern for imperative camera moves).
export function ItemMap({ item }: { item?: StacDoc }) {
  const mapRef = useRef<MapRef>(null);
  const bbox = item?.bbox;

  useEffect(() => {
    if (bbox && mapRef.current) {
      const [w, s, e, n] = bbox;
      mapRef.current.fitBounds([[w, s], [e, n]], { padding: 40, maxZoom: 12, duration: 600 });
    }
  }, [bbox]);

  const pm = pmtilesLink(item);
  const pmLayer = pm?.["pmtiles:layers"]?.[0] ?? item?.id;

  return (
    <MapGL
      ref={mapRef}
      mapLib={maplibregl}
      initialViewState={{ longitude: -111.7, latitude: 39.3, zoom: 5.3 }}
      mapStyle="https://tiles.openfreemap.org/styles/liberty"
      style={{ width: "100%", height: "100%" }}
    >
      {item?.geometry && (
        <Source id="footprint" type="geojson" data={{ type: "Feature", properties: {}, geometry: item.geometry }}>
          <Layer id="fp-fill" type="fill" paint={{ "fill-color": "#2b6cdf", "fill-opacity": 0.12 }} />
          <Layer id="fp-line" type="line" paint={{ "line-color": "#2b6cdf", "line-width": 1.5 }} />
        </Source>
      )}
      {pm && pmLayer && (
        <Source id="pm" type="vector" url={`pmtiles://${pm.href}`}>
          <Layer id="pm-lyr" type="line" source-layer={pmLayer} paint={{ "line-color": "#d1491c", "line-width": 1 }} />
        </Source>
      )}
    </MapGL>
  );
}
