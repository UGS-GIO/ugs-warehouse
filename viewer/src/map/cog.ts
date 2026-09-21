import maplibregl from "maplibre-gl";
import { CappedMap } from "@/lib/lru";

// cog:// protocol backed by cog-tiler-wasm (opengeos): a serverless, TiTiler-style WASM tiler that
// picks the right COG overview per z/x/y tile and resamples it into the tile, so linework (fault
// teeth, contacts) stays crisp at every zoom. The previous client (@geomatico/maplibre-cog-protocol)
// handed MapLibre a too-coarse overview to hard-upscale, which hollowed out thin symbols. Our COGs
// are EPSG:3857 (its fast path) + CORS + range-enabled, so it range-reads them straight off the CDN.
// Registered once, lazily; sources are cached by href so multiple COG layers share one registration.
//
// Shared by the item-detail COG map (CogMap) and the multi-layer overlay map (ItemMap): both mount
// `cog://<href>/{z}/{x}/{y}` raster sources that resolve against the single registration below.

type CtModule = typeof import("cog-tiler-wasm");
type CogSource = Awaited<ReturnType<CtModule["openCog"]>>;
type RenderOptions = import("cog-tiler-wasm").RenderOptions;

// Pre-rendered RGB(A) map plates: pass bands 1-3 straight through (identity rescale, no colormap/
// stretch) so the cartography renders in its own colors.
const RGB_RENDER = { bidx: [1, 2, 3], rescale: [[0, 255], [0, 255], [0, 255]] as [number, number][] };

// Render options chosen from the source's band count. Nearly all pub COGs are 3-/4-band RGB(A) plates
// (RGB_RENDER). A single- or two-band COG (paletted, or a continuous scientific raster / DEM per
// docs/RASTER_SPEC.md) must NOT be forced through the 3-band read: bands 2-3 are out of range and
// renderTilePNG throws. For those we pass no opts and let the tiler apply its own default (band 1 via
// its palette or the default colormap). Data-driven single-band styling (picking a colormap, a
// statistics-based rescale) is a separate feature, not this render-fidelity fix.
export const renderOptsForBands = (bands: number): RenderOptions => (bands >= 3 ? RGB_RENDER : {});

// Tile URL: `cog://<href>/{z}/{x}/{y}`, with optional trailing query (some MapLibre request paths /
// transform hooks append cache-busting or auth params; without tolerating them the handler would
// silently return blank tiles). The three trailing numeric segments are z/x/y; everything before
// them (including the href's own `https://` slashes) is the COG href.
const TILE_RE = /^cog:\/\/(.+)\/(\d+)\/(\d+)\/(\d+)(?:\?.*)?$/;

/** Parse a `cog://` tile URL into its href + z/x/y, or null if it isn't one. */
export const parseCogTileUrl = (
  url: string,
): { href: string; z: number; x: number; y: number } | null => {
  const m = TILE_RE.exec(url);
  if (!m) return null;
  return { href: m[1], z: Number(m[2]), x: Number(m[3]), y: Number(m[4]) };
};

let ct: CtModule | null = null;

// Bound the per-href source cache so a long browsing session can't grow it without limit; 32 is well
// above the real concurrent-COG count, so normal use never evicts. cog-tiler-wasm's CogSource exposes
// no close()/free(), so dropping the reference for GC is the only cleanup available. Mirrors the
// pmtiles archive cache in pmtiles-protocol.ts.
const COG_SOURCE_CAP = 32;
const sources = new CappedMap<string, Promise<CogSource>>(COG_SOURCE_CAP);

// Open (and cache) a COG by href; the tiler range-reads it directly, so one open serves every tile.
const openCogCached = (href: string): Promise<CogSource> => {
  let p = sources.get(href);
  if (!p) {
    if (!ct) return Promise.reject(new Error("cog-tiler-wasm is not initialized"));
    p = ct.openCog(href);
    // Don't cache a failed open; allow a later retry. Only evict THIS promise: the cap may have
    // already evicted it and a newer open re-cached the href, and we must not drop that live entry.
    p.catch(() => { if (sources.get(href) === p) sources.delete(href); });
    sources.set(href, p);
  }
  return p;
};

let cogReady: Promise<void> | null = null;
export const ensureCogProtocol = (): Promise<void> =>
  (cogReady ??= (async () => {
    try {
      ct = await import("cog-tiler-wasm");
      await ct.init(); // load the wasm modules once
      maplibregl.addProtocol("cog", async (params) => {
        const t = parseCogTileUrl(params.url);
        if (!t) return { data: new Uint8Array() };
        try {
          const source = await openCogCached(t.href);
          const opts = renderOptsForBands(source.levels[0]?.bands ?? 0);
          const png = await source.renderTilePNG(t.z, t.x, t.y, opts);
          return { data: png };
        } catch (e) {
          console.error("cog tile", t.href, t.z, t.x, t.y, e);
          return { data: new Uint8Array() }; // a blank tile beats tearing the whole layer down
        }
      });
    } catch (e) {
      cogReady = null; // a transient import/init blip shouldn't wedge every later COG; allow a retry
      throw e;
    }
  })());

// A COG's geographic extent as a lon/lat bbox, for camera-fit; replaces getCogMetadata().bbox.
// cog-tiler-wasm exposes it on the opened source (already lon/lat, warped from source CRS if needed).
export const getCogBounds = async (
  href: string,
): Promise<[number, number, number, number] | null> => {
  await ensureCogProtocol();
  try {
    const b = (await openCogCached(href)).boundsLonLat;
    return b && b.length >= 4 ? [b[0], b[1], b[2], b[3]] : null;
  } catch {
    return null;
  }
};
