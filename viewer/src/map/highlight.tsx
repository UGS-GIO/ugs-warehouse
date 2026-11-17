// What a click picked, drawn over the map: yellow on a dark casing for the selected feature, blue
// for the one a list row is hovering. The same look as the UGS map viewer, so the apps match.
import { useSyncExternalStore } from "react";
import { Layer, type LayerProps, type MapGeoJSONFeature, type MapLayerMouseEvent, Source } from "react-map-gl/maplibre";

import { GEOM_FILTER } from "./map-model";

type Tone = "selected" | "hover";
const PAINT: Record<Tone, { fill: object; casing?: object; line: object; halo?: object; dot: object }> = {
  selected: {
    // A light fill, so the colors of the map under the feature stay readable.
    fill: { "fill-color": "#FFFF00", "fill-opacity": 0.1 },
    casing: { "line-color": "rgba(0,0,0,0.5)", "line-width": 6 },
    line: { "line-color": "#FFFF00", "line-width": 3 },
    halo: { "circle-color": "rgba(0,0,0,0.5)", "circle-radius": 12 },
    dot: { "circle-color": "rgba(255,255,0,0.5)", "circle-radius": 10, "circle-stroke-color": "#FFFF00", "circle-stroke-width": 2 },
  },
  hover: {
    fill: { "fill-color": "rgba(30,107,255,0.25)" },
    line: { "line-color": "#1E6BFF", "line-width": 3 },
    dot: { "circle-color": "rgba(30,107,255,0.25)", "circle-radius": 11, "circle-stroke-color": "#1E6BFF", "circle-stroke-width": 3 },
  },
};

const layersOf = (id: string, tone: Tone): LayerProps[] => {
  const p = PAINT[tone];
  return [
    { id: `${id}-fill`, type: "fill", filter: GEOM_FILTER.fill, paint: p.fill },
    ...(p.casing ? [{ id: `${id}-casing`, type: "line", filter: GEOM_FILTER.line, paint: p.casing }] : []),
    { id: `${id}-line`, type: "line", filter: GEOM_FILTER.line, paint: p.line },
    ...(p.halo ? [{ id: `${id}-halo`, type: "circle", filter: GEOM_FILTER.point, paint: p.halo }] : []),
    { id: `${id}-dot`, type: "circle", filter: GEOM_FILTER.point, paint: p.dot },
  ] as LayerProps[];
};

/** The GL layer ids a highlight draws, bottom first, to keep at the top of a restacked map. */
export const highlightLayerIds = (id: string, tone: Tone): string[] => layersOf(id, tone).map((l) => String(l.id));

// One feature to light, by the source it is drawn from and its attributes. Publication layers are
// tiled without feature ids, so a feature is matched on its attributes; drawing from the source
// rather than the clicked tile's geometry keeps the outline whole across tile edges.
// A feature with no attribute to find it by is drawn from its own (tile-clipped) geometry instead.
export type HighlightTarget =
  | { source: string; sourceLayer?: string; props: Record<string, unknown> }
  | { geometry: GeoJSON.Geometry };

const matchOf = (props: Record<string, unknown>) => Object.entries(props)
  .filter(([, v]) => typeof v === "string" || typeof v === "number" || typeof v === "boolean")
  .map(([k, v]) => ["==", ["get", k], v]);

export function Highlight({ id, tone, target }: { id: string; tone: Tone; target?: HighlightTarget | null }) {
  if (target && "geometry" in target) {
    return (
      <Source key="geometry" id={`${id}-geometry`} type="geojson" data={{ type: "Feature", properties: {}, geometry: target.geometry }}>
        {layersOf(id, tone).map((l) => <Layer key={l.id} {...l} />)}
      </Source>
    );
  }
  const match = target ? matchOf(target.props) : [];
  // With no attribute to match on, a filter would light every feature of the layer.
  if (!target || !match.length) return null;
  return (
    <>
      {layersOf(id, tone).map((l) => (
        <Layer key={`${target.source}:${l.id}`} {...({ ...l, source: target.source, "source-layer": target.sourceLayer,
          filter: ["all", (l as { filter: unknown }).filter, ...match] } as unknown as LayerProps)} />
      ))}
    </>
  );
}

// The feature a list row is hovering. A module store, so only the map's hover layer re-renders.
let hovered: HighlightTarget | null = null;
const listeners = new Set<() => void>();
export function setHovered(g: HighlightTarget | null): void {
  if (g === hovered) return;
  hovered = g;
  listeners.forEach((f) => f());
}
const subscribe = (f: () => void) => { listeners.add(f); return () => { listeners.delete(f); }; };
export const useHovered = (): HighlightTarget | null => useSyncExternalStore(subscribe, () => hovered, () => null);

// Every feature of `layers` under a click, front first. A fingertip is wider than a 1 px line, so
// with nothing right under the point it looks in a box around it. Layers not yet added are skipped:
// one unknown id makes maplibre answer nothing for the rest.
export function pickFeatures(e: MapLayerMouseEvent, layers: string[]): MapGeoJSONFeature[] {
  const ids = layers.filter((id) => e.target.getLayer(id));
  if (!ids.length) return [];
  const under = e.target.queryRenderedFeatures(e.point, { layers: ids });
  if (under.length) return under;
  const r = matchMedia("(pointer: coarse)").matches ? 12 : 3;
  const { x, y } = e.point;
  return e.target.queryRenderedFeatures([[x - r, y - r], [x + r, y + r]], { layers: ids });
}
