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
    //
    // injectManifest, not generateSW: the worker is written out in src/sw.ts because offline layers
    // need a fetch handler that answers HTTP Range from local storage, which no Workbox strategy
    // expresses. The routes are the same either way; only the authoring moves.
    VitePWA({
      strategies: "injectManifest",
      srcDir: "src",
      filename: "sw.ts",
      // The worker calls skipWaiting/clientsClaim itself; this only drives the client registration.
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
      injectManifest: {
        globPatterns: ["**/*.{js,css,html,svg,woff2}"],
        // The duckdb-wasm loader and its `eh` worker (~1 MB) are precached so a table saved for
        // offline can open; the 36 MB .wasm is cached only when someone saves one (offline/engine.ts).
        // The `mvp` worker serves browsers without Wasm exceptions, which offline tables skip.
        // public/stac and public/pmtiles are the gitignored local dev fixtures.
        globIgnores: ["**/duckdb-browser-mvp*", "stac/**", "pmtiles/**"],
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
