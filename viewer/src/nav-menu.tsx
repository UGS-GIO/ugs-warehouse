// The view switcher as a hamburger, for widths where the tab row can't fit (it used to scroll
// sideways off the edge). Dependency-free: the viewer carries no menu library, and this needs a
// button, a list, Escape and an outside click.
import { useEffect, useRef, useState } from "react";

export type NavPage = { id: string; label: string; onSelect: () => void };

export function NavMenu({ pages, current }: { pages: NavPage[]; current: string }) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div ref={root} className="relative">
      <button
        type="button"
        aria-label="Views"
        aria-expanded={open}
        aria-haspopup="menu"
        onClick={() => setOpen((o) => !o)}
        className="flex h-9 w-9 items-center justify-center rounded-md border border-border bg-card text-foreground hover:bg-accent"
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden>
          <path d="M3 6h18M3 12h18M3 18h18" />
        </svg>
      </button>
      {open && (
        <div role="menu" className="absolute right-0 z-50 mt-1 min-w-44 rounded-md border border-border bg-card p-1 shadow-lg">
          {pages.map((p) => (
            <button
              key={p.id}
              type="button"
              role="menuitem"
              aria-current={p.id === current ? "page" : undefined}
              onClick={() => { p.onSelect(); setOpen(false); }}
              className={`flex w-full items-center rounded px-2 py-1.5 text-left text-[13px] hover:bg-accent ${p.id === current ? "text-primary" : "text-foreground"}`}
            >
              {p.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
