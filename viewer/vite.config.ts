import { execSync } from "node:child_process";
import { fileURLToPath, URL } from "node:url";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { tanstackRouter } from "@tanstack/router-plugin/vite";
import { defineConfig } from "vite";
import { VitePWA } from "vite-plugin-pwa";

// Build stamp — git short hash + the HEAD commit's date, so the date and hash
// always describe the same commit. Falls back gracefully if git is unavailable
// (e.g. a tarball build). Surfaced in the viewer header (App.tsx).
const sh = (cmd: string, fallback: string) => {
  try {
    return execSync(cmd, { stdio: ["ignore", "pipe", "ignore"] }).toString().trim();
  } catch {
    return fallback;
  }
};
const BUILD_HASH = sh("git describe --tags --always --dirty", "dev");
// HEAD commit date; if git is absent (tarball build), use the build date.
const BUILD_DATE = sh("git log -1 --format=%cd --date=short", new Date().toISOString().slice(0, 10));

// base has to be an ABSOLUTE mount path, not "./": path routes mean the document's own directory
// varies with the route (/map vs /discover/x), so a relative asset URL resolves differently per
// page. It also feeds the router's basepath (src/routes.tsx). "/" for Firebase; the review and
// preview builds pass their own prefix with --base.
export default defineConfig({
  // tanstackRouter must precede react(): it generates routeTree.gen.ts from src/routes/ and the
  // react plugin has to see the generated output.
  plugins: [
    tanstackRouter({ target: "react", autoCodeSplitting: true }),
    react(),
    tailwindcss(),
    // Service worker + manifest. Scope, start_url and the precache manifest all derive from `base`,
    // so the review and preview builds get a SW scoped to their own prefix with no extra config.
    VitePWA({
      // The catalog is read-mostly and the app must never pin an old deploy: take the new SW as soon
      // as it is installed rather than waiting for every tab to close.
      registerType: "autoUpdate",
      includeAssets: ["favicon.svg", "robots.txt", "icons/*.png"],
      manifest: {
        name: "UGS Warehouse",
        short_name: "UGS Warehouse",
        description: "Utah Geological Survey data catalog and map viewer.",
        theme_color: "#2765e4",
        background_color: "#ffffff",
        display: "standalone",
        icons: [
          { src: "icons/icon-192.png", sizes: "192x192", type: "image/png" },
          { src: "icons/icon-512.png", sizes: "512x512", type: "image/png" },
          { src: "icons/icon-maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
        ],
      },
      workbox: {
        globPatterns: ["**/*.{js,css,html,svg,woff2}"],
        // The duckdb-wasm engine (a 197 KiB loader plus two ~800 KiB workers) only pays off next to
        // its 35 MB .wasm, which globPatterns already leaves out, so precaching it reaches a dead
        // end. Match `duckdb-browser-*` and not `duckdb-*`: the latter also catches our own 1 KiB
        // duckdb.ts wrapper, which /discover statically imports, and dropping it fails the whole
        // route offline. public/stac and public/pmtiles are the gitignored local dev fixtures.
        globIgnores: ["**/duckdb-browser-*", "stac/**", "pmtiles/**"],
        cleanupOutdatedCaches: true,
        // The review deploy serves /api/comments from the same origin; a navigation fallback must
        // never answer for it.
        navigateFallbackDenylist: [/^\/api\//],
        runtimeCaching: [
          {
            // Catalog JSON on the CDN, matched by path so it holds for the public catalog
            // (warehouse/stac), the review catalog (review/stac) and a ?catalog= override alike.
            // Range requests are excluded: PMTiles and COGs are single files read by 206 partials,
            // and the Cache API cannot serve a range from a stored full response.
            urlPattern: ({ url, request }) =>
              url.pathname.includes("/stac/")
              && url.pathname.endsWith(".json")
              && !request.headers.has("range"),
            // StaleWhileRevalidate, not CacheFirst: items.json already ships max-age 60 + SWR 600,
            // and a catalog pinned forever is worse than no catalog.
            handler: "StaleWhileRevalidate",
            options: {
              cacheName: "ugs-stac-json",
              expiration: { maxEntries: 500, maxAgeSeconds: 60 * 60 * 24 * 30 },
              cacheableResponse: { statuses: [0, 200] },
            },
          },
        ],
      },
    }),
  ],
  base: "/",
  // "@" is src/ — a cross-folder import says where it comes from without counting ../ hops.
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  define: {
    __BUILD_HASH__: JSON.stringify(BUILD_HASH),
    __BUILD_DATE__: JSON.stringify(BUILD_DATE),
  },
  // Allow importing the canonical docs/*.md (one level above viewer/) for markdown-rendered pages.
  server: { fs: { allow: [".."] } },
  // Default environment stays node — component tests opt into jsdom with a `@vitest-environment`
  // docblock, so the pure tests keep running in milliseconds.
  test: { setupFiles: ["./src/test-setup.ts"] },
});
