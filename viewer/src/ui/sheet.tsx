import type { KeyboardEvent } from "react";
import { GRAB, noMenu } from "./sheet-chrome";

/** The phone sheet's grab handle. A button too, so each detent is a tap or an arrow key away, not
 *  only a drag (WCAG 2.5.7): a tap steps, the arrows move one detent up or down. */
export function SheetGrabber({ label, onStep, onArrow }: {
  label: string; onStep: () => void; onArrow: (step: 1 | -1) => void;
}) {
  const arrow = (e: KeyboardEvent) => {
    const step = e.key === "ArrowUp" ? 1 : e.key === "ArrowDown" ? -1 : 0;
    if (!step) return;
    e.preventDefault();
    onArrow(step);
  };
  return (
    <button type="button" onContextMenu={noMenu} onClick={onStep} onKeyDown={arrow} aria-label={label}
      className={`flex shrink-0 cursor-grab touch-none items-center justify-center py-3 ${GRAB}`}>
      <span className="h-1.5 w-10 rounded-full bg-border" />
    </button>
  );
}
