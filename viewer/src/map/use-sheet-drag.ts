// The phone sheet's drag. One gesture, three ways in: the handle and the tab bar (pointer events)
// and the sheet's own scrolling content (touch events). It only counts once it moves SLOP px, so a
// tap on a tab stays a tap, and on release a flick carries it one detent further.
import type { MouseEvent, PointerEvent as ReactPointerEvent, RefObject } from "react";
import { useCallback, useRef } from "react";

import { clampSize, contentTakesDrag, DETENTS, releaseDetent } from "./map-model";

const SLOP = 8;

type Drag = { y0: number; h0: number; height: number; y: number; t: number; speed: number; moved: boolean };

// The drag writes the sheet's height straight to the DOM: no React render per frame. On release
// it hands back to the `height: N%` the shell renders for the detent.
export function useSheetDrag(areaRef: RefObject<HTMLDivElement | null>, detent: number, setDetent: (d: number) => void) {
  const sheetRef = useRef<HTMLDivElement>(null);
  const drag = useRef<Drag | null>(null);
  const dragged = useRef(false);

  const heightOf = (d: Drag, y: number) => clampSize(d.h0 + d.y0 - y, d.height * 0.06, d.height * 0.94, d.h0);

  const begin = (y: number) => {
    const height = areaRef.current?.clientHeight ?? window.innerHeight;
    drag.current = { y0: y, h0: height * DETENTS[detent], height, y, t: performance.now(), speed: 0, moved: false };
    dragged.current = false;
  };
  const move = (y: number) => {
    const d = drag.current;
    if (!d || (!d.moved && Math.abs(y - d.y0) < SLOP)) return;
    const now = performance.now();
    const speed = (d.y - y) / d.height / Math.max(now - d.t, 1) * 1000;   // sheet-heights/s, + is up
    Object.assign(d, { y, t: now, speed: 0.7 * speed + 0.3 * d.speed, moved: true });
    const el = sheetRef.current;
    if (!el) return;
    el.style.transition = "none";
    el.style.height = `${heightOf(d, y)}px`;
  };
  const end = () => {
    const d = drag.current;
    drag.current = null;
    if (!d?.moved) return;
    dragged.current = true;
    const next = releaseDetent(heightOf(d, d.y) / d.height, d.speed);
    // Set here too: when the detent doesn't change, React has no new style to write.
    const el = sheetRef.current;
    if (el) Object.assign(el.style, { transition: "", height: `${DETENTS[next] * 100}%` });
    setDetent(next);
  };

  const onPointerDown = (e: ReactPointerEvent) => {
    begin(e.clientY);
    const off = new AbortController();
    const finish = () => { end(); off.abort(); };
    window.addEventListener("pointermove", (ev) => move(ev.clientY), { signal: off.signal });
    window.addEventListener("pointerup", finish, { signal: off.signal });
    window.addEventListener("pointercancel", finish, { signal: off.signal });
  };

  // A stable ref callback reads the latest render through this, so its listeners attach once.
  const latest = useRef({ begin, move, end, detent });
  latest.current = { begin, move, end, detent };

  // Touch, not pointer: only a non-passive touchmove can stop the browser taking it as a scroll.
  const contentRef = useCallback((el: HTMLElement | null) => {
    if (!el) return;
    let x0 = 0, y0 = 0, decided = false, dragging = false;
    const off = new AbortController();
    el.addEventListener("touchstart", (e) => {
      ({ clientX: x0, clientY: y0 } = e.touches[0]);
      dragging = false;
      decided = handlesOwnTouch(e.target, el);
    }, { passive: true, signal: off.signal });
    el.addEventListener("touchmove", (e) => {
      const { clientX: x, clientY: y } = e.touches[0];
      if (!decided) {
        decided = true;
        dragging = contentTakesDrag(x - x0, y - y0, scrolledToTop(e.target, el), latest.current.detent);
        if (dragging) latest.current.begin(y0);
      }
      if (!dragging) return;
      e.preventDefault();
      latest.current.move(y);
    }, { passive: false, signal: off.signal });
    const finish = () => { if (dragging) latest.current.end(); dragging = false; };
    el.addEventListener("touchend", finish, { signal: off.signal });
    el.addEventListener("touchcancel", finish, { signal: off.signal });
    return () => off.abort();
  }, []);

  // The click a drag ending on a tab fires is not a tap. A keyboard click (detail 0) always is.
  const wasDrag = (e: MouseEvent) => e.detail > 0 && dragged.current;

  return { sheetRef, onPointerDown, contentRef, wasDrag };
}

// Nested scrollers count too: a table scrolled down inside the sheet should scroll, not drag.
function scrolledToTop(target: EventTarget | null, root: HTMLElement) {
  for (let n = target as HTMLElement | null; n && n !== root.parentElement; n = n.parentElement) {
    if (n.scrollTop > 0) return false;
  }
  return true;
}

// A touch-action: none element (a reorder grip) runs its own drag; the sheet stays out of it.
function handlesOwnTouch(target: EventTarget | null, root: HTMLElement) {
  for (let n = target as HTMLElement | null; n && n !== root; n = n.parentElement) {
    if (getComputedStyle(n).touchAction === "none") return true;
  }
  return false;
}
