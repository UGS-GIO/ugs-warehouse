// The device's position and compass heading. The position comes from the map's GeolocateControl,
// the one watcher behind the blue dot, follow mode and "What's under me".
import { useSyncExternalStore } from "react";

export type Fix = { lng: number; lat: number };

let fix: Fix | null = null;
const fixListeners = new Set<() => void>();

export function setFix(next: Fix): void {
  fix = next;
  fixListeners.forEach((fn) => fn());
}
export const lastFix = (): Fix | null => fix;
export const useFix = (): Fix | null => useSyncExternalStore(
  (fn) => { fixListeners.add(fn); return () => { fixListeners.delete(fn); }; }, () => fix, () => null);

type Orientation = { alpha: number | null; absolute: boolean; webkitCompassHeading?: number };

/** Degrees clockwise from north, or null when the event has no true heading. iOS reports it as
 *  webkitCompassHeading; elsewhere an absolute alpha counts counter-clockwise. */
export function headingOf(e: Orientation): number | null {
  if (typeof e.webkitCompassHeading === "number" && !Number.isNaN(e.webkitCompassHeading)) return e.webkitCompassHeading;
  return e.absolute && e.alpha !== null ? (360 - e.alpha) % 360 : null;
}

// One window listener for every subscriber, attached while anyone listens and the compass is allowed.
let heading: number | null = null;
const headingListeners = new Set<() => void>();
// iOS asks for the compass from a tap; elsewhere it is allowed from the start.
type AskingEvent = { requestPermission?: () => Promise<"granted" | "denied"> };
const asking = (): AskingEvent | undefined =>
  typeof DeviceOrientationEvent === "undefined" ? undefined : (DeviceOrientationEvent as unknown as AskingEvent);
let allowed = typeof asking()?.requestPermission !== "function";
const EVENT = typeof window !== "undefined" && "ondeviceorientationabsolute" in window
  ? "deviceorientationabsolute" : "deviceorientation";

const onOrientation = (e: Event) => {
  const next = headingOf(e as unknown as Orientation);
  if (next === null || (heading !== null && Math.abs(next - heading) < 2)) return;
  heading = Math.round(next);
  headingListeners.forEach((fn) => fn());
};
const attach = (on: boolean) => {
  if (on && allowed) window.addEventListener(EVENT, onOrientation);
  else window.removeEventListener(EVENT, onOrientation);
};

export const useHeading = (): number | null => useSyncExternalStore((fn) => {
  if (!headingListeners.size) attach(true);
  headingListeners.add(fn);
  return () => { headingListeners.delete(fn); if (!headingListeners.size) attach(false); };
}, () => heading, () => null);

/** Ask for the compass. Call from a tap: iOS refuses the request otherwise. */
export async function askHeading(): Promise<void> {
  const ev = asking();
  if (allowed || !ev?.requestPermission) return;
  try { allowed = (await ev.requestPermission()) === "granted"; } catch { return; }
  if (allowed && headingListeners.size) attach(true);
}
