// "Developer" section of the item-detail page: view the exact STAC item.json the CDN served (the
// ground truth behind the rendered panels), copy it, or open the raw .json. A modal — closable via
// the header button or Escape; on open it moves focus in and locks body scroll, and on close it
// restores focus to the trigger. Ported from the reference StacJsonDialog onto the viewer's tokens.
import { useEffect, useMemo, useRef, useState } from "react";

import type { StacDoc } from "@/stac";

// The item's own STAC URL (rel=self) — an "open .json" target where the catalog carries one.
const selfHrefOf = (item: StacDoc): string | undefined =>
  item.links?.find((l) => l.rel === "self")?.href;

export function StacJson({ item, title = "STAC item JSON" }: { item: StacDoc; title?: string }) {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const json = useMemo(() => JSON.stringify(item, null, 2), [item]);
  const self = selfHrefOf(item);
  const closeRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  useEffect(() => () => clearTimeout(timer.current), []);

  useEffect(() => {
    if (!open) return;
    const restoreTo = document.activeElement as HTMLElement | null;
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { setOpen(false); return; }
      // Trap Tab inside the dialog so focus can't wander to the inert page behind it.
      if (e.key === "Tab") {
        const f = panelRef.current?.querySelectorAll<HTMLElement>('a[href], button:not([disabled])');
        if (!f || f.length === 0) return;
        const first = f[0], last = f[f.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
      restoreTo?.focus?.();
    };
  }, [open]);

  const copy = () => {
    navigator.clipboard?.writeText(json);
    setCopied(true);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setCopied(false), 1200);
  };

  return (
    <>
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" onClick={() => setOpen(true)}
          className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-sm font-medium text-foreground transition-colors hover:border-primary hover:text-primary">
          View STAC JSON
        </button>
        {self && (
          <a href={self} target="_blank" rel="noopener"
            className="text-sm text-primary no-underline hover:underline">Open .json ↗</a>
        )}
      </div>

      {open && (
        <div role="dialog" aria-modal="true" aria-label={title}
          className="fixed inset-0 z-50 flex items-center justify-center p-4">
          <div aria-hidden onClick={() => setOpen(false)} className="absolute inset-0 bg-black/50" />
          <div ref={panelRef} className="relative z-10 flex max-h-[85vh] w-full max-w-3xl flex-col rounded-lg border border-border bg-card shadow-xl">
            <div className="flex items-center justify-between gap-2 border-b border-border px-4 py-3">
              <h2 className="text-sm font-semibold text-card-foreground">{title}</h2>
              <div className="flex items-center gap-1.5">
                <button type="button" onClick={copy}
                  className="rounded border border-border bg-card px-2 py-1 text-xs text-foreground hover:border-primary">
                  {copied ? "copied" : "copy"}
                </button>
                <button ref={closeRef} type="button" aria-label="Close" onClick={() => setOpen(false)}
                  className="flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-hover hover:text-foreground">
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
                    strokeLinecap="round" aria-hidden><path d="M6 6l12 12M18 6L6 18" /></svg>
                </button>
              </div>
            </div>
            <pre className="overflow-auto p-4 font-mono text-xs leading-relaxed text-card-foreground">{json}</pre>
          </div>
        </div>
      )}
    </>
  );
}
