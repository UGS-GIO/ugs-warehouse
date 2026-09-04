import { execSync } from "node:child_process";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { tanstackRouter } from "@tanstack/router-plugin/vite";
import { defineConfig } from "vite";

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
  plugins: [tanstackRouter({ target: "react", autoCodeSplitting: true }), react(), tailwindcss()],
  base: "/",
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
