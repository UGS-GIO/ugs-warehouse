import { execSync } from "node:child_process";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
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

// base: "./" so the built static bundle works under any CDN path.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: "./",
  define: {
    __BUILD_HASH__: JSON.stringify(BUILD_HASH),
    __BUILD_DATE__: JSON.stringify(BUILD_DATE),
  },
});
