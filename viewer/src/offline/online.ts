// Whether the browser believes it has a connection, as a hook. navigator.onLine can say "online"
// on a network that reaches nothing, but "offline" is reliable, and offline is the case the UI
// has to explain.
import { useSyncExternalStore } from "react";

const subscribe = (fn: () => void) => {
  window.addEventListener("online", fn);
  window.addEventListener("offline", fn);
  return () => {
    window.removeEventListener("online", fn);
    window.removeEventListener("offline", fn);
  };
};

export const useOnline = (): boolean =>
  useSyncExternalStore(subscribe, () => navigator.onLine, () => true);

/**
 * True when an error is the network failing rather than the server answering. A connection with
 * no route out (a field hotspot, a captive portal) still reports navigator.onLine === true, so a
 * failed fetch is the better signal that we are, in effect, offline.
 */
export const isNetworkError = (e: unknown): boolean =>
  e instanceof TypeError && /fetch|network|load failed/i.test(e.message);
