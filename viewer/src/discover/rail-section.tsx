import { type ReactNode, useState } from "react";

import { Chevron } from "@/ui/chevron";

// A collapsible facet-rail group: its heading, a summary of what's set, then its rows.
export function RailSection({ label, summary, defaultOpen, children }: {
  label: string; summary?: string; defaultOpen: boolean; children: ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section className="border-t border-border px-2 py-2">
      <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open}
        className="flex w-full items-center justify-between gap-2 px-1 py-0.5 text-left pointer-coarse:min-h-11">
        <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{label}</span>
        <span className="flex min-w-0 items-center gap-1 text-xs text-muted-foreground">
          {summary && <span className="truncate">{summary}</span>}
          <Chevron open={open} />
        </span>
      </button>
      {open && children}
    </section>
  );
}
