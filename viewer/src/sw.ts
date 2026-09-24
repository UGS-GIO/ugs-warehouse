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
import { assemble, parseBlockMeta } from "./offline/cog-blocks";
import { finished } from "./offline/guards";
import { fileNameFor, isLive, versionOf } from "./offline/opfs-name";
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

// OpenFreeMap's style, sprite, glyphs and TileJSON: the files the map asks for on every load, cached
// as it asks for them so the basemap's look survives going offline. Tiles are deliberately NOT here:
// their terms prohibit automated collection, so offline tiles come from our own quad archives.
registerRoute(
  ({ url }) => url.hostname === "tiles.openfreemap.org"
    && (/^\/(styles|sprites|fonts)\//.test(url.pathname) || url.pathname === "/planet"),
  new StaleWhileRevalidate({
    cacheName: "ugs-basemap-style",
    plugins: [
      new ExpirationPlugin({ maxEntries: 300, maxAgeSeconds: 60 * 60 * 24 * 30 }),
      new CacheableResponsePlugin({ statuses: [200] }),
    ],
  }),
);

// The table engine's .wasm, once someone saves a table for offline (offline/engine.ts puts it in
// this cache). Never cached on the way past: at 36 MB it is kept only for people who asked.
registerRoute(
  ({ url }) => url.origin === self.location.origin && /\/assets\/duckdb-eh-[^/]+\.wasm$/.test(url.pathname),
  async ({ request }) => (await caches.match(request, { cacheName: "ugs-engine" })) ?? fetch(request),
);

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

/** A COG or table saved by area (offline/cog-area.ts): its block directory and the file's real size. */
async function storedCogArea(url: string) {
  try {
    const root = await navigator.storage.getDirectory();
    const done = await finished(await (await root.getDirectoryHandle("cogs")).getDirectoryHandle(fileNameFor(url)));
    const meta = done && parseBlockMeta(done.meta);
    return meta && { dir: done.dir, ...meta };
  } catch {
    return null;
  }
}

/**
 * Answer a range read of a COG saved by area from its stored blocks. A read that needs a block the
 * area did not save (the reader panned outside it) goes to the network, or fails cleanly offline
 * so that part of the plate is simply blank.
 */
async function serveCogArea(request: Request, cog: NonNullable<Awaited<ReturnType<typeof storedCogArea>>>) {
  // A block not saved comes from the network, but only from the version the saved blocks were cut
  // from: bytes of a newer file at the same offsets would be garbage to the reader. The update
  // check on Offline data re-saves the area against the new version.
  const miss = async () => {
    const res = await fetch(request).catch(() => null);
    if (!res) return new Response(null, { status: 504 });
    const v = versionOf(res.headers);
    return cog.version && v && v !== cog.version ? new Response(null, { status: 504 }) : res;
  };
  const type = contentTypeFor(new URL(request.url).pathname);
  const resolved = resolveRange(request.headers.get("range"), cog.size);
  // DuckDB sizes a file with HEAD, then probes range support with a ranged HEAD (bytes=0-), which
  // must come back 206. The stored size answers both offline.
  if (request.method === "HEAD") {
    return new Response(null, { status: rangeStatus(resolved), headers: rangeHeaders(resolved, type) });
  }
  if (resolved.kind !== "partial") return miss();   // a whole-file read needs the whole file
  const blocks = new Map<number, Uint8Array>();
  for (let b = Math.floor(resolved.start / cog.block); b <= Math.floor(resolved.end / cog.block); b++) {
    const file = await cog.dir.getFileHandle(String(b)).then((h) => h.getFile(), () => null);
    if (!file) return miss();
    blocks.set(b, new Uint8Array(await file.arrayBuffer()));
  }
  const body = assemble(resolved.start, resolved.end, cog.block, (i) => blocks.get(i) ?? null);
  if (!body) return miss();
  return new Response(body, {
    status: 206, headers: rangeHeaders(resolved, type),
  });
}

async function serveStored(request: Request): Promise<Response> {
  const file = await storedFile(request.url);
  if (!file) {
    // A COG or a table saved by area (cog-area.ts, table-area.ts): stored as blocks.
    const cog = /\.(tiff?|parquet)$/i.test(new URL(request.url).pathname) ? await storedCogArea(request.url) : null;
    return cog ? serveCogArea(request, cog) : fetch(request);
  }

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
  const url = new URL(request.url);
  if (!STORABLE.test(url.pathname)) return;
  if (isLive(url)) return;   // the published file itself (opfs-name.ts live)
  event.respondWith(serveStored(request));
});
