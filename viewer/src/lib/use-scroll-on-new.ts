import { type RefObject, useEffect, useRef } from "react";

const NEAREST: ScrollIntoViewOptions = { behavior: "smooth", block: "nearest" };

/** Scroll `ref` into view when `key` becomes a NEW non-null value.
 *
 *  Seeded from the mount value, so a selection carried in across a remount (e.g. an item page
 *  switching between its page and drawer layouts) does not jump the viewport with no click behind
 *  it. The timeout defers past the same commit's layout — a section that expands as part of this
 *  render has its final height by then. */
export function useScrollOnNew(key: unknown, ref: RefObject<HTMLElement | null>,
                               opts: ScrollIntoViewOptions = NEAREST): void {
  const scrolledFor = useRef(key);
  useEffect(() => {
    if (!key || scrolledFor.current === key) return;
    scrolledFor.current = key;
    const t = setTimeout(() => ref.current?.scrollIntoView(opts), 0);
    return () => clearTimeout(t);
    // `ref`/`opts` are stable per call site; re-running on a new object identity would re-scroll.
  }, [key]);  // eslint-disable-line react-hooks/exhaustive-deps
}
