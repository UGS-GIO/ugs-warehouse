// What is kept on this device, as one store the whole app reads.
//
// Only this app changes what is on the device, so there is nothing to fetch or go stale: the store
// re-reads storage after each change it is told about (a download finishing, a delete) and every
// control re-renders from the same snapshot. Network questions about saved things (is a newer
// version published, what would this cost) stay TanStack queries; this is the local half.
import { useSyncExternalStore } from "react";
import { loadStoredAreas, removeArea } from "./area-store";
import type { Bbox } from "./guards";
import { setStoredBasemaps } from "./basemap";
import { listCogAreas, removeCogArea } from "./block-store";
import { engineBytes, removeEngine as dropEngine } from "./engine";
import * as opfs from "./opfs";
import * as queue from "./queue";

/** A layer, plate or table saved for part of the map: tiles for PMTiles, blocks for a COG or table. */
export type StoredArea = { url: string; bytes: number; version?: string; bboxes: Bbox[]; kind: "tiles" | "blocks" };

export type OfflineState = {
  /** False until storage has been read once; lists are empty until then, not "nothing saved". */
  ready: boolean;
  files: opfs.StoredFile[];
  areas: StoredArea[];
  /** Downloads stopped part-way, with what they hold so far. */
  partials: opfs.StoredFile[];
  /** The table engine's size when it is on the device, else 0. */
  engineBytes: number;
  /** Whether the browser has promised not to clear this storage on its own. */
  persisted: boolean;
  space: { usage?: number; quota?: number };
};

const EMPTY: OfflineState = {
  ready: false, files: [], areas: [], partials: [], engineBytes: 0, persisted: false, space: {},
};

let state = EMPTY;
const listeners = new Set<() => void>();

export const subscribe = (fn: () => void) => {
  listeners.add(fn);
  // The first reader triggers the first read; nothing is read for a page that never shows it.
  if (!state.ready) void refresh();
  return () => { listeners.delete(fn); };
};
export const snapshot = () => state;

async function read(): Promise<OfflineState> {
  const [files, tiles, blocks, partials, engine, space, persisted] = await Promise.all([
    opfs.list(), loadStoredAreas(), listCogAreas(), opfs.listPartials(), engineBytes(), opfs.quota(),
    navigator.storage?.persisted?.().catch(() => false) ?? Promise.resolve(false),
  ]);
  const area = (a: { url: string; bytes: number; version?: string; bboxes: Bbox[] }, kind: StoredArea["kind"]) =>
    ({ url: a.url, bytes: a.bytes, version: a.version, bboxes: a.bboxes, kind });
  return {
    ready: true, files, partials, engineBytes: engine, space, persisted,
    areas: [...tiles.map((t) => area(t, "tiles")), ...blocks.map((b) => area(b, "blocks"))],
  };
}

// One read at a time; a change during a read asks for another after it, so the last word wins.
let reading: Promise<void> | null = null;
let again = false;

/** Re-read what is on the device and tell every subscriber. */
export function refresh(): Promise<void> {
  if (reading) { again = true; return reading; }
  reading = (async () => {
    do {
      again = false;
      state = await read().catch(() => ({ ...state, ready: true }));
      // The basemap protocol routes tiles to saved archives from this set.
      setStoredBasemaps(state.files.map((f) => f.url));
      listeners.forEach((fn) => fn());
    } while (again);
  })().finally(() => { reading = null; });
  return reading;
}

/** Resolves once storage has been read, for code that must not act on the empty first snapshot. */
export const whenReady = (): Promise<void> => (state.ready ? Promise.resolve() : refresh());

export const useOffline = (): OfflineState => useSyncExternalStore(subscribe, snapshot, snapshot);

// ---- changes, each followed by a re-read ----

export async function removeFiles(urls: string[]): Promise<void> {
  await Promise.all(urls.map((u) => opfs.remove(u)));
  await refresh();
}

export async function removeAreas(rows: Pick<StoredArea, "url" | "kind">[]): Promise<void> {
  await Promise.all(rows.map((r) => (r.kind === "blocks" ? removeCogArea(r.url) : removeArea(r.url))));
  await refresh();
}

export async function removeEngine(): Promise<void> {
  await dropEngine();
  await refresh();
}

export async function removeJob(id: string): Promise<void> {
  await queue.remove(id);
  await refresh();
}

// Every download attempt that ends changes what is stored: a finished file, or a partial to show.
queue.onSettled(() => { void refresh(); });
