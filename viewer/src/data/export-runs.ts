// Which exports are in flight, and which were cancelled. DuckDB and GDAL cannot be interrupted,
// so cancelling suppresses the delivery: an abandoned export must not drop a file minutes later.
// The ticket is taken BEFORE the pre-flight, the slow part and the usual moment to cancel.
import type { ExportFormat } from "./export-formats";

export type ExportRun = { id: number; stem: string; fmt: ExportFormat };

let ticket = 0;
const cancelled = new Set<number>();
// Every live run: the panel is keyed per item, so navigating away can leave two in flight.
const live = new Map<number, ExportRun>();
let snapshot: readonly ExportRun[] = [];
const listeners = new Set<() => void>();

// Rebuilt only on change — useSyncExternalStore needs a stable reference between updates.
const publish = () => { snapshot = [...live.values()]; listeners.forEach((l) => l()); };

export const beginExport = (): number => ++ticket;

/** Suppress one run's delivery, never any other's. */
export const cancelExport = (id: number): void => {
  cancelled.add(id);
  if (live.delete(id)) publish();
};

/** True once, for a run cancelled before it started; consumed so the set cannot grow. */
export const consumeIfCancelled = (id: number): boolean => cancelled.delete(id);

export const isCancelled = (id: number): boolean => cancelled.has(id);

export const startRun = (run: ExportRun): void => { live.set(run.id, run); publish(); };

export const endRun = (id: number): void => {
  cancelled.delete(id);
  if (live.delete(id)) publish();
};

export const currentExports = (): readonly ExportRun[] => snapshot;

export const subscribeExport = (l: () => void): (() => void) => {
  listeners.add(l);
  return () => { listeners.delete(l); };
};
