// `md` — the one breakpoint the app branches on in JS (layout shells, table vs cards). A media
// query is an external store, so subscribe to it: no effect, and no first paint at the wrong size.
import { useSyncExternalStore } from "react";

const MD = window.matchMedia("(min-width: 768px)");
const subscribe = (onChange: () => void) => {
  MD.addEventListener("change", onChange);
  return () => MD.removeEventListener("change", onChange);
};

export const useIsDesktop = (): boolean => useSyncExternalStore(subscribe, () => MD.matches);
