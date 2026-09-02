// Breakpoints the app branches on in JS (layout shells, table vs cards, the Discover map pane). A
// media query is an external store, so subscribe to it: no effect, and no first paint at the wrong size.
import { useSyncExternalStore } from "react";

const MD = window.matchMedia("(min-width: 768px)");
const LG = window.matchMedia("(min-width: 1024px)");
const subMd = (onChange: () => void) => {
  MD.addEventListener("change", onChange);
  return () => MD.removeEventListener("change", onChange);
};
const subLg = (onChange: () => void) => {
  LG.addEventListener("change", onChange);
  return () => LG.removeEventListener("change", onChange);
};

export const useIsDesktop = (): boolean => useSyncExternalStore(subMd, () => MD.matches);
// `lg` — the Discover view only MOUNTS its map pane at/above this, so maplibre never loads (and no
// WebGL context spins up in a hidden 0×0 box) on a phone/tablet where the map is hidden anyway.
export const useIsWide = (): boolean => useSyncExternalStore(subLg, () => LG.matches);
