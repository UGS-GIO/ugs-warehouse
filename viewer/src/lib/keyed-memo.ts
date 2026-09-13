import { useMemo } from "react";

/** Memoize on an explicit key rather than on the value's identity.
 *
 *  The catalog's item arrays are rebuilt on every render, so memoizing on them re-indexes thousands
 *  of docs per keystroke. A key that changes only when the DATA changes is the whole point, and
 *  writing that as `useMemo(fn, [key])` means an exhaustive-deps disable at every call site. This
 *  owns the one disable, and names the contract: everything `fn` reads must be covered by `key`. */
export function useKeyedMemo<T>(key: string, fn: () => T): T {
  // eslint-disable-next-line react-hooks/exhaustive-deps
  return useMemo(fn, [key]);
}

type IndexRow = { id: string; index?: { items?: unknown[] } | null };

/** Key for a set of loaded collection indexes: which collections, and how many items each holds.
 *
 *  It tracks arrival, not content — a doc REPLACED in place, with the count unchanged, does not move
 *  this key, and anything memoized on it keeps the old value. That is the trade for not re-indexing
 *  on every render, and it holds because the indexes are append-on-load and never mutated in place. */
export const indexRowsKey = (rows: readonly IndexRow[]): string =>
  rows.map((r) => `${r.id}:${r.index?.items?.length ?? 0}`).join("|");
