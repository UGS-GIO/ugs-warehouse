// Service-worker route predicates, kept out of vite.config.ts so they are unit-testable.
// Imported by the VitePWA `runtimeCaching` config at build time; never bundled into the app.

/** The fields we use out of Workbox's RouteMatchCallbackOptions. Workbox always passes a request
    when matching a fetch; it is optional here so the unit test can match on a URL alone. */
export type RouteMatch = { url: URL; request?: Request };

/**
 * Catalog JSON on the CDN. Matched by path rather than host so one rule covers the public catalog
 * (warehouse/stac), the review catalog (review/stac) and a `?catalog=` override alike.
 *
 * Range requests are excluded: PMTiles and COGs are single files read by 206 partials, and the
 * Cache API cannot serve a range out of a stored full response, so a match here would break them.
 */
export const isCatalogJson = ({ url, request }: RouteMatch): boolean =>
  url.pathname.includes("/stac/")
  && url.pathname.endsWith(".json")
  && !request?.headers.has("range");

/**
 * What the service worker installs up front: the page, its entry chunk and styles, and the small
 * files beside it (icons, logo). Every other chunk is cached when first loaded, and all of them
 * once someone saves something for offline (sw.ts), so a visitor who never goes offline downloads
 * only what they use. Manifest URLs are relative to the app's base.
 */
export const isShell = (url: string): boolean => !url.includes("/") || /^assets\/index-[^/]+\.(js|css)$/.test(url);

/** A layer's style (ugs-styles), so a layer saved for offline draws in its own colors, not the fallback. */
export const isLayerStyle = ({ url }: RouteMatch): boolean =>
  url.hostname === "maps-assets.geology.utah.gov" && url.pathname.startsWith("/styles/styles/")
  && url.pathname.endsWith(".json");

/** A built chunk or asset: content-hashed, so a cached copy never goes stale. */
export const isAppAsset = ({ url }: RouteMatch, origin: string): boolean =>
  url.origin === origin && url.pathname.includes("/assets/") && !url.pathname.endsWith(".wasm");
