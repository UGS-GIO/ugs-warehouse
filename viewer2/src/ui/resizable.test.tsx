// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { ResizeHandle } from "./resizable";
import { type ResizeAxis, useResizable } from "./use-resizable";

const SPEC = { initial: 200, min: 100, max: 400 };
const KEY = "test.paneSize";

function Pane({ axis }: { axis: ResizeAxis }) {
  const r = useResizable(KEY, SPEC, axis);
  return (
    <>
      <output>{r.size}</output>
      <ResizeHandle resizable={r} label="Resize pane" />
    </>
  );
}

// Each case mounts more than one pane, so a fresh mount replaces the last rather than stacking.
const mount = (axis: ResizeAxis) => { cleanup(); render(<Pane axis={axis} />); };
const size = () => Number(screen.getByRole("status").textContent);
const handle = () => screen.getByRole("separator");
// jsdom's PointerEvent is incomplete, so drive the listeners with the MouseEvent they read from.
const drag = (from: { x?: number; y?: number }, to: { x?: number; y?: number }) => {
  fireEvent.pointerDown(handle(), { clientX: from.x ?? 0, clientY: from.y ?? 0 });
  fireEvent(window, new MouseEvent("pointerup", { clientX: to.x ?? 0, clientY: to.y ?? 0 }));
};

describe("useResizable", () => {
  beforeEach(() => localStorage.clear());

  it("starts at the stored size, and at the default when the store is junk or empty", () => {
    mount("x");
    expect(size()).toBe(200);
    localStorage.setItem(KEY, "310");
    mount("x");
    expect(size()).toBe(310);
    localStorage.setItem(KEY, "not-a-number");
    mount("x");
    expect(size()).toBe(200);
  });

  it("grows the way its axis points", () => {
    mount("x");
    drag({ x: 0 }, { x: 50 });
    expect(size()).toBe(250);           // left-anchored: rightward drag widens

    localStorage.clear();
    mount("x-left");
    drag({ x: 0 }, { x: -50 });
    expect(size()).toBe(250);           // a drawer: LEFTWARD drag widens

    localStorage.clear();
    mount("y");
    drag({ y: 0 }, { y: -50 });
    expect(size()).toBe(250);           // bottom-anchored dock: upward drag grows it
  });

  it("clamps a drag past either end and persists what it committed", () => {
    mount("x");
    drag({ x: 0 }, { x: 9999 });
    expect(size()).toBe(400);
    expect(localStorage.getItem(KEY)).toBe("400");
    drag({ x: 0 }, { x: -9999 });
    expect(size()).toBe(100);
  });

  it("resizes on the arrow keys that match the axis, and ignores the rest", () => {
    mount("x-left");
    fireEvent.keyDown(handle(), { key: "ArrowLeft" });
    expect(size()).toBe(216);
    fireEvent.keyDown(handle(), { key: "ArrowRight" });
    expect(size()).toBe(200);
    fireEvent.keyDown(handle(), { key: "ArrowUp" });
    expect(size()).toBe(200);
  });

  it("exposes the size on the separator, so a screen reader can read the drag", () => {
    mount("x-left");
    fireEvent.keyDown(handle(), { key: "ArrowLeft" });
    expect(handle().getAttribute("aria-valuenow")).toBe("216");
    expect(handle().getAttribute("aria-valuemin")).toBe("100");
    expect(handle().getAttribute("aria-valuemax")).toBe("400");
    expect(handle().getAttribute("aria-orientation")).toBe("vertical");
  });

  it("clears the body cursor when a drag is cancelled, not just when it ends", () => {
    mount("x");
    fireEvent.pointerDown(handle(), { clientX: 0 });
    expect(document.body.style.cursor).toBe("col-resize");
    fireEvent(window, new MouseEvent("pointercancel", { clientX: 30 }));
    expect(document.body.style.cursor).toBe("");
  });
});
