// The draggable edge of a resizable pane — the one place the separator's a11y contract lives.
import { type Resizable } from "./use-resizable";

/** The draggable edge itself: a hit area wider than the visible grip, positioned by the caller. */
export function ResizeHandle({ resizable, label, className = "" }: {
  resizable: Resizable;
  label: string;          // what it resizes, e.g. "Resize layer list"
  className?: string;     // placement only (absolute/inset/margins) — size + cursor live here
}) {
  const { size, axis, spec, onPointerDown, onKeyDown } = resizable;
  const vertical = axis !== "y"; // a vertical separator sits between LEFT/RIGHT panes
  return (
    <div
      role="separator"
      aria-orientation={vertical ? "vertical" : "horizontal"}
      aria-label={label}
      aria-valuenow={Math.round(size)}
      aria-valuemin={spec.min}
      aria-valuemax={spec.max}
      aria-valuetext={`${Math.round(size)} pixels`}
      tabIndex={0}
      onPointerDown={onPointerDown}
      onKeyDown={onKeyDown}
      // touch-none: without it a touch drag scrolls the page instead of moving the edge.
      className={`group flex touch-none items-center justify-center focus:outline-none ${
        vertical ? "w-4 cursor-col-resize" : "h-3 cursor-row-resize"} ${className}`}
    >
      <span className={`rounded-full bg-muted-foreground/30 transition-colors group-hover:bg-primary group-focus:bg-primary ${
        vertical ? "h-10 w-1.5" : "h-1.5 w-10"}`} />
    </div>
  );
}
