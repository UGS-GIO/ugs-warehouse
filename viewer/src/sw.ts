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
import { fileNameFor } from "./offline/opfs-name";
import { contentTypeFor, rangeHeaders, rangeStatus, resolveRange, STORABLE } from "./offline/range";
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

// ---- Offline artifacts: serve a downloaded file, Range and all ----
//
// Every artifact we publish is a single file read by HTTP Range: PMTiles archives, COGs, GeoParquet.
// Answering those requests here, from the copy in OPFS, is what makes a downloaded layer work with
// no connection — and it works for every reader at once, without any of them exposing a hook.
// pmtiles offers a FileSource, geotiff through the geomatico protocol offers nothing, duckdb-wasm
// offers nothing; the network is the one interface all three share.

/** The stored file for a request URL, or null when it was never downloaded. */
async function storedFile(url: string): Promise<File | null> {
  try {
    const root = await navigator.storage.getDirectory();
    const dir = await root.getDirectoryHandle("layers");
    return await (await dir.getFileHandle(fileNameFor(url))).getFile();
  } catch {
    return null;   // not stored, or storage unavailable — either way, use the network
  }
}

async function serveStored(request: Request): Promise<Response> {
  const file = await storedFile(request.url);
  if (!file) return fetch(request);

  const resolved = resolveRange(request.headers.get("range"), file.size);
  const headers = rangeHeaders(resolved, contentTypeFor(new URL(request.url).pathname));
  const status = rangeStatus(resolved);
  if (status === 416) return new Response(null, { status, headers });
  // HEAD carries the headers and no body; a reader sizes the archive with it before ranging.
  if (request.method === "HEAD") return new Response(null, { status, headers });

  const body = resolved.kind === "partial"
    ? file.slice(resolved.start, resolved.end + 1)
    : file;
  return new Response(body, { status, headers });
}

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET" && request.method !== "HEAD") return;
  // Narrow by extension before touching storage, so the common request never pays an OPFS lookup.
  // Anything not stored falls through to the network inside the handler.
  if (!STORABLE.test(new URL(request.url).pathname)) return;
  event.respondWith(serveStored(request));
});
