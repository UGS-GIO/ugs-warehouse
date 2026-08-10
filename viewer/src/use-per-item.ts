import { useCallback, useState } from "react";

/** State scoped to one item: when `itemId` changes it reads back as `initial`. A reset effect does
 *  this a frame late, so the new item would render once carrying the old item's highlight. */
export function usePerItem<T>(itemId: string, initial: T) {
  const [held, setHeld] = useState<{ id: string; v: T }>(() => ({ id: itemId, v: initial }));
  // React's "adjusting state during render" — re-renders before commit, so nothing stale paints.
  // Without it the value would only be masked while another item shows, and come back on return.
  if (held.id !== itemId) setHeld({ id: itemId, v: initial });
  const set = useCallback((next: T | ((prev: T) => T)) => {
    setHeld((h) => {
      const prev = h.id === itemId ? h.v : initial;
      return { id: itemId, v: typeof next === "function" ? (next as (p: T) => T)(prev) : next };
    });
  }, [itemId, initial]);
  return [held.id === itemId ? held.v : initial, set] as const;
}
