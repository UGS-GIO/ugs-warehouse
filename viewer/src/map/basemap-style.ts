// Streets and Light: our Utah Protomaps extract (scripts/build_basemap.py) with the
// @protomaps/basemaps flavors. Kept out of offline/basemap.ts so map-less pages don't load them.
import { layers, namedFlavor } from "@protomaps/basemaps";
import type { SourceSpecification, StyleSpecification } from "maplibre-gl";
import { stateUrl } from "@/offline/basemap";

export const GLYPHS = "https://maps-assets.geology.utah.gov/styles/fonts/{fontstack}/{range}.pbf";

const ATTRIBUTION = '<a href="https://protomaps.com" target="_blank">Protomaps</a> '
  + '© <a href="https://www.openstreetmap.org/copyright" target="_blank">OpenStreetMap</a>';

export type Flavor = "light" | "white";

/** basemap:// picks saved archives first (offline/basemap-protocol.ts); the main map uses it. */
export const ROUTED: SourceSpecification = { type: "vector", tiles: ["basemap://{z}/{x}/{y}"], minzoom: 0, maxzoom: 14 };
/** The statewide file straight off the CDN, for maps that never go offline (previews). */
export const DIRECT: SourceSpecification = { type: "vector", url: `pmtiles://${stateUrl()}` };

export function protomapsStyle(flavor: Flavor, source: SourceSpecification = ROUTED): StyleSpecification {
  return {
    version: 8,
    glyphs: GLYPHS,
    sprite: new URL(`${import.meta.env.BASE_URL}basemap-${flavor}`, location.origin).href,
    sources: { protomaps: { attribution: ATTRIBUTION, ...source } as SourceSpecification },
    layers: layers("protomaps", namedFlavor(flavor), { lang: "en" }),
  };
}
