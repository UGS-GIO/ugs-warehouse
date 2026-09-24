// Offline basemap: the archives, the quad grid they are cut on, and the style that points at them.
// The protocol that reads them is basemap-protocol.ts, apart because it needs maplibre's projection
// and this module is also loaded by pages with no map (Offline data).
//
// The basemap is one statewide overview archive for low zooms plus one archive per 7.5-minute quad
// for high zooms (scripts/build_basemap.py). The style keeps a SINGLE source, `basemap://{z}/{x}/{y}`,
// and this module decides per tile where the bytes come from:
//
//   stored in OPFS  → our archive (the service worker serves its ranges from disk)
//   otherwise       → OpenFreeMap, exactly as before this existed
//   neither         → an empty tile, so an offline map draws blank rather than erroring
//
// Stored-first rather than ours-first because our extract is clipped to Utah: served to everyone,
// the neighbouring states would go blank at the border. This way a user who downloads nothing sees
// no change at all, and one who does sees online what they will see offline.
//
// The quad arithmetic mirrors src/ugs_warehouse/basemap.py; both are tested against the same
// USGS codes so the viewer never asks for an archive the build did not name.
import type { StyleSpecification } from "maplibre-gl";
import { isRecord } from "./guards";

export const BASEMAP_BASE =
  import.meta.env.VITE_BASEMAP_BASE
  || "https://maps-assets.geology.utah.gov/basemap/";

const CELL = 0.125;                  // 7.5 minutes
const ROWS = "abcdefgh";
export const OVERVIEW_MAXZOOM = 10;  // must match OVERVIEW_MAXZOOM in basemap.py

export const stateUrl = (base = BASEMAP_BASE) => `${base}utah.pmtiles`;
export const overviewUrl = (base = BASEMAP_BASE) => `${base}overview.pmtiles`;
export const quadUrl = (code: string, base = BASEMAP_BASE) => `${base}quads/${code}.pmtiles`;

export type Quad = { code: string; west: number; south: number; east: number; north: number };

/** The 7.5-minute quad containing a point, named by USGS Ohio code (e.g. 40111g8). */
export function quadAt(lon: number, lat: number): Quad {
  const blockLat = Math.floor(lat);
  const blockLon = Math.floor(-lon);
  const row = Math.min(Math.floor((lat - blockLat) / CELL), 7);
  const col = Math.min(Math.floor((-lon - blockLon) / CELL), 7);
  const south = blockLat + row * CELL;
  const east = -(blockLon + col * CELL);
  const code = `${String(blockLat).padStart(2, "0")}${String(blockLon).padStart(3, "0")}${ROWS[row]}${col + 1}`;
  return { code, west: east - CELL, south, east, north: south + CELL };
}

/** Stored partial-basemap files the statewide one makes redundant: the overview and every quad. */
export function redundantWithState(saved: Iterable<string>, base = BASEMAP_BASE): string[] {
  return [...saved].filter((u) => u === overviewUrl(base) || u.startsWith(`${base}quads/`));
}

/** Every quad a bbox touches, for "download what I'm looking at". */
export function quadsInBbox([w, s, e, n]: [number, number, number, number]): Quad[] {
  const out = new Map<string, Quad>();
  for (let lat = Math.floor(s / CELL) * CELL + CELL / 2; lat < n + CELL / 2; lat += CELL) {
    for (let lon = Math.floor(w / CELL) * CELL + CELL / 2; lon < e + CELL / 2; lon += CELL) {
      const q = quadAt(lon, Math.min(lat, n));
      out.set(q.code, q);
    }
  }
  return [...out.values()];
}

// ---- protocol ----

/** Archive URLs downloaded to this device. Kept current by the offline store (store.ts). */
const stored = new Set<string>();
export const storedBasemaps: ReadonlySet<string> = stored;
export function setStoredBasemaps(urls: Iterable<string>): void {
  stored.clear();
  for (const u of urls) if (u.startsWith(BASEMAP_BASE)) stored.add(u);
}

// ---- style ----

type Style = { sources: Record<string, { type: string; url?: string; attribution?: string }> } & Record<string, unknown>;

const OFM_ATTRIBUTION = '<a href="https://openfreemap.org" target="_blank">OpenFreeMap</a> '
  + '<a href="https://www.openmaptiles.org/" target="_blank">© OpenMapTiles</a> '
  + 'Data from <a href="https://www.openstreetmap.org/copyright" target="_blank">OpenStreetMap</a>';

/**
 * An OpenFreeMap style with its vector source routed through `basemap://`. Every layer, the sprite
 * and the glyphs stay exactly as OpenFreeMap ships them; only where the tiles come from changes,
 * which the tiles allow because ours are built to the same OpenMapTiles schema.
 */
/** A fetched style document with what rerouteStyle and MapLibre need: version 8, sources, layers. */
export const isStyle = (v: unknown): v is StyleSpecification =>
  isRecord(v) && v.version === 8 && isRecord(v.sources) && Array.isArray(v.layers);

export function rerouteStyle<S extends Style>(style: S): S {
  const src = style.sources.openmaptiles;
  if (src?.type !== "vector") return style;
  return {
    ...style,
    sources: {
      ...style.sources,
      openmaptiles: {
        type: "vector", tiles: ["basemap://{z}/{x}/{y}"], minzoom: 0, maxzoom: 14,
        attribution: src.attribution ?? OFM_ATTRIBUTION,
      },
    },
  };
}

/** What the map draws when no basemap style can be had at all: data layers over nothing. */
export const BLANK_STYLE = {
  version: 8 as const,
  glyphs: "https://maps-assets.geology.utah.gov/styles/fonts/{fontstack}/{range}.pbf",
  sources: {},
  layers: [],
};
