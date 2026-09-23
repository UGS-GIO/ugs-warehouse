/// <reference lib="webworker" />
// The viewer's service worker.
//
// Written out rather than generated (VitePWA `injectManifest`, not `generateSW`) because the
// offline story needs a fetch handler Workbox cannot express: our artifacts are single files read
// by HTTP Range, and serving those from local storage means answering 206 ourselves. The routes
// below are the same ones the generated worker had.
import { CacheableResponsePlugin } from "workbox-cacheable-response";
import { clientsClaim } from "workbox-core";
import { ExpirationPlugin } from "workbox-expiration";
import { cleanupOutdatedCaches, createHandlerBoundToURL, precacheAndRoute } from "workbox-precaching";
import { NavigationRoute, registerRoute } from "workbox-routing";
import { StaleWhileRevalidate } from "workbox-strategies";
import { isCatalogJson } from "./sw-routes";

declare const self: ServiceWorkerGlobalScope & { __WB_MANIFEST: Array<{ url: string; revision: string | null }> };

// Matches registerType "autoUpdate": a new worker takes over rather than waiting for every tab to
// close, so a deploy is never pinned by one long-lived tab.
self.skipWaiting();
clientsClaim();

precacheAndRoute(self.__WB_MANIFEST);
cleanupOutdatedCaches();

// SPA deep links. `/api/` is denied because the review deploy serves comments from this origin.
registerRoute(new NavigationRoute(createHandlerBoundToURL("index.html"), { denylist: [/^\/api\//] }));

// StaleWhileRevalidate, not CacheFirst: items.json already ships max-age 60 + SWR 600, and a
// catalog pinned forever is worse than no catalog. 200 only — an opaque response on this route is
// a failed request, and caching it would serve the failure back as catalog data.
registerRoute(isCatalogJson, new StaleWhileRevalidate({
  cacheName: "ugs-stac-json",
  plugins: [
    new ExpirationPlugin({ maxEntries: 500, maxAgeSeconds: 60 * 60 * 24 * 30 }),
    new CacheableResponsePlugin({ statuses: [200] }),
  ],
}));
