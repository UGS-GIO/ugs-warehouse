/// <reference types="vite/client" />
/// <reference types="vite-plugin-pwa/client" />

// Bare CSS subpath export (no `.css` extension) — TS6 can't resolve it without a hint.
declare module "@utahdts/utah-design-system-header/css";

// Build stamp injected by vite.config.ts `define` (git hash + HEAD commit date).
declare const __BUILD_HASH__: string;
declare const __BUILD_DATE__: string;
