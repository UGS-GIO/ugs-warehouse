import { type ReactNode, useId } from "react";

// One grey box of the layer page's side column: a small heading, an optional note, then its rows.
export function Panel({ title, note, children }: { title: string; note?: string; children: ReactNode }) {
  const id = useId();
  return (
    <section aria-labelledby={id} className="rounded-lg border border-border bg-muted p-3">
      <h3 id={id} className="mb-1.5 text-sm font-semibold text-muted-foreground">{title}</h3>
      {note && <p className="mb-2 text-xs text-muted-foreground">{note}</p>}
      {children}
    </section>
  );
}
