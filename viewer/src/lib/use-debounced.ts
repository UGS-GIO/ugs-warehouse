import { useEffect, useState } from "react";

/** `value`, but only after it has stopped changing for `ms`.
 *
 *  A timer is a real external system, so this is one of the few places an effect belongs. Keeping it
 *  here means callers *derive* from the settled value instead of writing state from inside a
 *  timeout — the shape that kept producing reset-on-change effects (PR #301 review). */
export function useDebounced<T>(value: T, ms = 300): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setSettled(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return settled;
}
