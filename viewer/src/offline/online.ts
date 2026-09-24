// Whether the browser believes it has a connection, as a hook: TanStack Query's onlineManager, the
// same signal that pauses and resumes its queries (seeded from navigator.onLine in query-client.ts).
// "Online" can mean a network that reaches nothing, but "offline" is reliable, and offline is the
// case the UI has to explain.
import { onlineManager } from "@tanstack/react-query";
import { useSyncExternalStore } from "react";

const subscribe = (fn: () => void) => onlineManager.subscribe(fn);

export const useOnline = (): boolean =>
  useSyncExternalStore(subscribe, () => onlineManager.isOnline(), () => true);

/**
 * True when an error is the network failing rather than the server answering. A connection with
 * no route out (a field hotspot, a captive portal) still reports navigator.onLine === true, so a
 * failed fetch is the better signal that we are, in effect, offline.
 */
export const isNetworkError = (e: unknown): boolean =>
  e instanceof TypeError && /fetch|network|load failed/i.test(e.message);
