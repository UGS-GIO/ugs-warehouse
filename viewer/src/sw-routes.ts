// Service-worker route predicates, kept out of vite.config.ts so they are unit-testable.
// Imported by the VitePWA `runtimeCaching` config at build time; never bundled into the app.

/** The shape Workbox hands a route-match callback (the fields we use). */
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
