// The last few results picked in the header search, kept in this browser only. What was picked,
// not what was typed, so a recent entry reopens the same place or item.
import { useSyncExternalStore } from "react";
import type { Bounds } from "@/map/place-locator";

export type RecentPick =
  | { kind: "place"; label: string; bounds: Bounds }
  | { kind: "layer" | "publication"; label: string; href: string; bbox?: Bounds };

const KEY = "ugs:recent-searches";
const MAX = 5;
const listeners = new Set<() => void>();
const NONE: RecentPick[] = [];
// useSyncExternalStore needs the same array back until storage changes.
let cache: { raw: string | null; picks: RecentPick[] } = { raw: null, picks: NONE };

const isPick = (p: unknown): p is RecentPick => {
  if (!p || typeof p !== "object") return false;
  const r = p as Record<string, unknown>;
  if (typeof r.label !== "string") return false;
  return r.kind === "place" ? Array.isArray(r.bounds) && r.bounds.length === 4
    : (r.kind === "layer" || r.kind === "publication") && typeof r.href === "string";
};

const keyOf = (p: RecentPick) => (p.kind === "place" ? `place:${p.label}` : `item:${p.href}`);

export function getRecent(): RecentPick[] {
  let raw: string | null;
  try { raw = localStorage.getItem(KEY); } catch { return NONE; }
  if (raw !== cache.raw) {
    let picks = NONE;
    try {
      const parsed: unknown = raw ? JSON.parse(raw) : null;
      if (Array.isArray(parsed)) picks = parsed.filter(isPick).slice(0, MAX);
    } catch { /* corrupt */ }
    cache = { raw, picks };
  }
  return cache.picks;
}

function save(picks: RecentPick[]): void {
  try {
    if (picks.length) localStorage.setItem(KEY, JSON.stringify(picks));
    else localStorage.removeItem(KEY);
  } catch { /* private window */ }
  listeners.forEach((fn) => fn());
}

export function addRecent(pick: RecentPick): void {
  save([pick, ...getRecent().filter((p) => keyOf(p) !== keyOf(pick))].slice(0, MAX));
}

export const clearRecent = (): void => save([]);

function subscribe(fn: () => void) {
  listeners.add(fn);
  return () => { listeners.delete(fn); };
}

export const useRecent = (): RecentPick[] => useSyncExternalStore(subscribe, getRecent, () => NONE);
