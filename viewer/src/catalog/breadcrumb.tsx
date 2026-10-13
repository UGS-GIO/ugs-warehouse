// The catalog trail ("Catalog / Publications / OFR-784DM"): a crumb with onClick is a link, the
// last one is the current page. Shared by the browse views and the item page.
import { C } from "@/ui/ui";

export type Crumb = { label: string; onClick?: () => void };

export function Breadcrumb({ crumbs, className = "mb-1 text-sm" }: { crumbs: Crumb[]; className?: string }) {
  return (
    <div className={className}>
      {crumbs.map((c, i) => (
        <span key={i}>
          {i > 0 && <span className={C.muted}> / </span>}
          {c.onClick ? <span className={C.crumb} onClick={c.onClick}>{c.label}</span>
            : <span className={i === crumbs.length - 1 ? "" : C.muted}>{c.label}</span>}
        </span>
      ))}
    </div>
  );
}
