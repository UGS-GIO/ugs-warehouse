// The catalog trail ("Catalog / Publications / OFR-784DM"): a crumb with onClick is a link, the
// last one is the current page. Shared by the browse views, the item page and the Discover drawer.
// Beside it sits "↑ <parent>", the crumb before the current page, also bound to Alt+↑.
import { useEffect, useRef } from "react";

import { C } from "@/ui/ui";

// Targets Alt+↑ must not take over: text entry and the map canvas.
const TYPING = "input, textarea, select, canvas, .maplibregl-map, [contenteditable]:not([contenteditable=false])";

export type Crumb = { label: string; onClick?: () => void };

export function Breadcrumb({ crumbs, className = "mb-1 text-sm" }: { crumbs: Crumb[]; className?: string }) {
  const parent = crumbs.length > 1 ? crumbs[crumbs.length - 2] : undefined;
  const up = parent?.onClick;
  const upRef = useRef(up);
  upRef.current = up;
  const hasUp = Boolean(up);
  useEffect(() => {
    if (!hasUp) return;
    const onKey = (e: KeyboardEvent) => {
      if (!e.altKey || e.ctrlKey || e.metaKey || e.shiftKey || e.key !== "ArrowUp" || e.defaultPrevented) return;
      // Alt+↑ is left alone while typing and over the map, which pans with the arrow keys.
      if ((e.target as Element | null)?.closest?.(TYPING)) return;
      e.preventDefault();
      upRef.current?.();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [hasUp]);

  return (
    <div className={className}>
      {crumbs.map((c, i) => (
        <span key={i}>
          {i > 0 && <span className={C.muted}> / </span>}
          {c.onClick ? <button type="button" className={`${C.crumb} bg-transparent p-0 hover:underline`} onClick={c.onClick}>{c.label}</button>
            : <span className={i === crumbs.length - 1 ? "" : C.muted}>{c.label}</span>}
        </span>
      ))}
      {up && (
        <button type="button" onClick={up} title="Up one level (Alt+↑)"
          className="ml-3 rounded px-1.5 py-0.5 text-primary hover:bg-hover">
          <span aria-hidden="true">↑</span> <span className="sr-only">Up to</span> {parent.label}
        </button>
      )}
    </div>
  );
}
