// Which exports are in flight, and which were cancelled. DuckDB and GDAL cannot be interrupted,
// so cancelling suppresses the delivery: an abandoned export must not drop a file minutes later.
// The ticket is taken BEFORE the pre-flight, the slow part and the usual moment to cancel.
import type { ExportFormat } from "./export-formats";

export type ExportRun = { id: number; stem: string; fmt: ExportFormat };

let ticket = 0;
// Tickets handed out and not yet finished. A cancel for anything else is a click on a button
// React has not unmounted yet, and recording it would leak an id nothing ever prunes.
const outstanding = new Set<number>();
const cancelled = new Set<number>();
// Every live run: the panel is keyed per item, so navigating away can leave two in flight.
const live = new Map<number, ExportRun>();
let snapshot: readonly ExportRun[] = [];
const listeners = new Set<() => void>();

// Rebuilt only on change — useSyncExternalStore needs a stable reference between updates.
const publish = () => { snapshot = [...live.values()]; listeners.forEach((l) => l()); };

export const beginExport = (): number => { outstanding.add(++ticket); return ticket; };

/** Suppress one run's delivery, never any other's. */
export const cancelExport = (id: number): void => {
  if (!outstanding.has(id)) return;
  cancelled.add(id);
  if (live.delete(id)) publish();
};

/** Whether this run was cancelled before it started. The caller ends the run on a true. */
export const consumeIfCancelled = (id: number): boolean => cancelled.has(id);

/** Tickets handed out and not yet ended — the set a cancel is allowed to act on. */
export const outstandingCount = (): number => outstanding.size;

/** Ids recorded as cancelled and not yet cleared. */
export const cancelledCount = (): number => cancelled.size;

export const isCancelled = (id: number): boolean => cancelled.has(id);

export const startRun = (run: ExportRun): void => { live.set(run.id, run); publish(); };

export const endRun = (id: number): void => {
  outstanding.delete(id);
  cancelled.delete(id);
  if (live.delete(id)) publish();
};

export const currentExports = (): readonly ExportRun[] => snapshot;

export const subscribeExport = (l: () => void): (() => void) => {
  listeners.add(l);
  return () => { listeners.delete(l); };
};
