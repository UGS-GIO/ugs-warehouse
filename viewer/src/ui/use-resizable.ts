// The size behind a drag-resizable pane: clamped, persisted, and driven by either a pointer drag
// or the arrow keys. Its separator is <ResizeHandle> in ./resizable — kept apart so this file stays
// hook-only (Fast Refresh) and so the map shell and the Discover drawer share one implementation.
import type { KeyboardEvent, PointerEvent } from "react";
import { useState } from "react";

import { clampSize } from "../map-model";

export type SizeSpec = { initial: number; min: number; max: number };

// Which way the pane GROWS as the pointer moves: "x" rightward (left-anchored pane), "x-left"
// leftward (right-anchored pane, e.g. a drawer), "y" upward (bottom-anchored dock).
export type ResizeAxis = "x" | "x-left" | "y";

// The key that makes the pane BIGGER, per axis; its opposite shrinks. Arrow keys follow the edge
// the way the pointer does, so the handle reads the same to a keyboard and a mouse.
const GROW_KEY: Record<ResizeAxis, string> = { x: "ArrowRight", "x-left": "ArrowLeft", y: "ArrowUp" };
const SHRINK_KEY: Record<ResizeAxis, string> = { x: "ArrowLeft", "x-left": "ArrowRight", y: "ArrowDown" };
const STEP = 16;

export type Resizable = ReturnType<typeof useResizable>;

export function useResizable(key: string, spec: SizeSpec, axis: ResizeAxis) {
  const { initial, min, max } = spec;
  const [size, setSize] = useState(() => clampSize(readStored(key), min, max, initial));
  const clamp = (n: number) => clampSize(n, min, max, initial);
  const commit = (n: number) => {
    const v = clamp(n);
    setSize(v);
    try { localStorage.setItem(key, String(v)); } catch { /* private mode / disabled storage */ }
  };

  const onPointerDown = (e: PointerEvent) => {
    e.preventDefault();
    const start = axis === "y" ? e.clientY : e.clientX;
    const startSize = size;
    const sizeAt = (ev: globalThis.PointerEvent) => startSize + delta(axis, start, ev);
    // pointercancel too: a lost pointer (touch cancelled, gesture stolen) must still tear the
    // listeners down and put the body cursor back, or the whole app stays in col-resize.
    const move = (ev: globalThis.PointerEvent) => setSize(clamp(sizeAt(ev)));
    const finish = (ev: globalThis.PointerEvent) => {
      commit(sizeAt(ev));
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", finish);
      window.removeEventListener("pointercancel", finish);
      document.body.style.cursor = "";
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", finish);
    window.addEventListener("pointercancel", finish);
    document.body.style.cursor = axis === "y" ? "row-resize" : "col-resize";
  };

  const onKeyDown = (e: KeyboardEvent) => {
    const step = e.key === GROW_KEY[axis] ? STEP : e.key === SHRINK_KEY[axis] ? -STEP : 0;
    if (!step) return;
    e.preventDefault(); // an arrow on a focused separator resizes; it must not also scroll the pane
    commit(size + step);
  };

  return { size, axis, spec, onPointerDown, onKeyDown };
}

// A stored value from an older build can be anything, and Number(null) is 0 — a finite number that
// would clamp to min, silently opening every pane at its NARROWEST instead of its default. Absent,
// blank and unreadable storage all have to come back NaN so clampSize falls back to `initial`.
function readStored(key: string): number {
  try {
    const raw = localStorage.getItem(key);
    return raw ? Number(raw) : NaN;
  } catch { return NaN; }
}

const delta = (axis: ResizeAxis, start: number, ev: globalThis.PointerEvent) =>
  axis === "x" ? ev.clientX - start : axis === "x-left" ? start - ev.clientX : start - ev.clientY;
