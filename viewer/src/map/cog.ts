import maplibregl from "maplibre-gl";

// cog:// protocol backed by cog-tiler-wasm (opengeos) — a serverless, TiTiler-style WASM tiler that
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

// Our pub COGs are pre-rendered RGB(A) map images, not data rasters — pass bands 1-3 straight through
// (identity rescale, no colormap/stretch) so the cartography renders in its own colors.
const RGB_RENDER = { bidx: [1, 2, 3], rescale: [[0, 255], [0, 255], [0, 255]] as [number, number][] };

// Tile URL: `cog://<href>/{z}/{x}/{y}`. The trailing three numeric segments are z/x/y; everything
// before them — including the href's own `https://` slashes — is the COG href.
const TILE_RE = /^cog:\/\/(.+)\/(\d+)\/(\d+)\/(\d+)$/;

let ct: CtModule | null = null;
const sources = new Map<string, Promise<CogSource>>();

// Open (and cache) a COG by href; the tiler range-reads it directly, so one open serves every tile.
const openCogCached = (href: string): Promise<CogSource> => {
  let p = sources.get(href);
  if (!p) {
    p = ct!.openCog(href);
    p.catch(() => sources.delete(href)); // don't cache a failed open — allow a later retry
    sources.set(href, p);
  }
  return p;
};

let cogReady: Promise<void> | null = null;
export const ensureCogProtocol = (): Promise<void> =>
  (cogReady ??= (async () => {
    ct = await import("cog-tiler-wasm");
    await ct.init(); // load the wasm modules once
    maplibregl.addProtocol("cog", async (params) => {
      const m = params.url.match(TILE_RE);
      if (!m) return { data: new Uint8Array() };
      const [, href, z, x, y] = m;
      try {
        const source = await openCogCached(href);
        const png = await source.renderTilePNG(Number(z), Number(x), Number(y), RGB_RENDER);
        return { data: png };
      } catch (e) {
        console.error("cog tile", href, z, x, y, e);
        return { data: new Uint8Array() }; // a blank tile beats tearing the whole layer down
      }
    });
  })());

// A COG's geographic extent as a lon/lat bbox, for camera-fit — replaces getCogMetadata().bbox.
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
