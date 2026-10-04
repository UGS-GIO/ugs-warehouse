// Data saver: on a slow or metered connection, load nothing the person did not ask for. Pages then
// hold back their previews (the item map) until tapped, so reaching a layer to download its data
// costs little more than the page itself.
//
// "auto" follows the browser: Chrome's data-saver flag, or a connection it rates 3G or slower.
// Safari and Firefox report neither, so the menu also offers "on" and "off", kept on this device.
import { useSyncExternalStore } from "react";

export type DataSaverPref = "auto" | "on" | "off";

const KEY = "ugs:data-saver";
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((fn) => fn());

// The Network Information API (Chrome, Android): not in TypeScript's DOM types.
type NetworkInformation = EventTarget & { saveData?: boolean; effectiveType?: string };
const isNetworkInformation = (v: unknown): v is NetworkInformation => v instanceof EventTarget;
const connection = (): NetworkInformation | undefined => {
  if (typeof navigator === "undefined" || !("connection" in navigator)) return undefined;
  const c = navigator.connection;
  return isNetworkInformation(c) ? c : undefined;
};

const SLOW = new Set(["slow-2g", "2g", "3g"]);

export function getPref(): DataSaverPref {
  try {
    const v = localStorage.getItem(KEY);
    return v === "on" || v === "off" ? v : "auto";
  } catch {
    return "auto";
  }
}

export function setPref(p: DataSaverPref): void {
  try { if (p === "auto") localStorage.removeItem(KEY); else localStorage.setItem(KEY, p); } catch { /* private window */ }
  emit();
}

/** Whether pages should hold back what the person did not ask for. */
export function dataSaverActive(): boolean {
  const pref = getPref();
  if (pref !== "auto") return pref === "on";
  const c = connection();
  return !!c && (c.saveData === true || (c.effectiveType !== undefined && SLOW.has(c.effectiveType)));
}

function subscribe(fn: () => void) {
  listeners.add(fn);
  const c = connection();
  c?.addEventListener("change", fn);
  return () => { listeners.delete(fn); c?.removeEventListener("change", fn); };
}

export const useDataSaver = (): boolean => useSyncExternalStore(subscribe, dataSaverActive, () => false);
export const useDataSaverPref = (): DataSaverPref => useSyncExternalStore(subscribe, getPref, () => "auto");
