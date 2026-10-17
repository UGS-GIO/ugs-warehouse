// The collapsible section that lists a layer's reference tables, shared by the item page, the
// feature card and the Map page's related-table launchers.
import { useState, type ReactNode } from "react";

// `defaultOpen` is where it starts (open when the reference tables are all there is, or one is
// already open); after that the user's toggle decides. Children get the current state so a caller
// can defer reading a table's rows until the section is open.
export function ReferenceTables({ titles, defaultOpen, className = "", summaryClassName =
  "cursor-pointer text-xs uppercase tracking-wide text-muted-foreground", children }: {
  titles: string[]; defaultOpen: boolean; className?: string; summaryClassName?: string;
  children: (open: boolean) => ReactNode;
}) {
  const [userOpen, setUserOpen] = useState<boolean>();
  const open = userOpen ?? defaultOpen;
  return (
    <details className={className} open={open} onToggle={(e) => setUserOpen(e.currentTarget.open)}>
      <summary className={summaryClassName}>
        Reference tables: <span className="normal-case">{[...new Set(titles)].join(", ")}</span>
      </summary>
      {children(open)}
    </details>
  );
}
