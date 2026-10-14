// The catalog trail ("Catalog / Publications / OFR-784DM"): a crumb with onClick is a link, the
// last one is the current page. Shared by the browse views, the item page and the Discover drawer.
// Beside it sits "↑ <parent>", the crumb before the current page, also bound to Alt+↑.
import { useEffect } from "react";

import { C } from "@/ui/ui";

export type Crumb = { label: string; onClick?: () => void };

export function Breadcrumb({ crumbs, className = "mb-1 text-sm" }: { crumbs: Crumb[]; className?: string }) {
  const parent = crumbs.length > 1 ? crumbs[crumbs.length - 2] : undefined;
  const up = parent?.onClick;
  useEffect(() => {
    if (!up) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.altKey && !e.ctrlKey && !e.metaKey && !e.shiftKey && e.key === "ArrowUp" && !e.defaultPrevented) {
        e.preventDefault();
        up();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [up]);

  return (
    <div className={className}>
      {crumbs.map((c, i) => (
        <span key={i}>
          {i > 0 && <span className={C.muted}> / </span>}
          {c.onClick ? <span className={C.crumb} onClick={c.onClick}>{c.label}</span>
            : <span className={i === crumbs.length - 1 ? "" : C.muted}>{c.label}</span>}
        </span>
      ))}
      {up && (
        <button type="button" onClick={up} aria-label={`Up to ${parent.label}`} title="Up one level (Alt+↑)"
          className="ml-3 rounded px-1.5 py-0.5 text-primary hover:bg-hover">
          ↑ {parent.label}
        </button>
      )}
    </div>
  );
}
