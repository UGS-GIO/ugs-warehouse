// Every mappable layer in one filterable list. The map view used to make you pick a collection,
// then hunt a checkbox in a stack of bordered cards — two clicks before anything drew, and no way
// to see what existed. Here the whole set is visible at once, active layers pinned on top.
//
// Rows are dense list rows, not cards: a state swatch, the title, and an info button. Items with no
// map asset never appear — they aren't layers, and they were most of the old noise.
import type { ReactNode } from "react";
import { useState } from "react";

// `layer` = a serving topic rather than a publication plate; those sort first and lead the list.
export type LayerRow = { id: string; href: string; title: string; group: string; layer: boolean };

export function LayerList({ rows, activeIds, colorOf, onToggle, onOpen, openId, legend }: {
  rows: LayerRow[];
  activeIds: string[];
  colorOf: (id: string) => string | undefined;
  onToggle: (id: string) => void;
  onOpen: (href: string) => void;
  openId?: string;
  legend?: ReactNode;   // what the active layers mean — belongs with them, not after the whole list
}) {
  const [filter, setFilter] = useState("");
  const q = filter.trim().toLowerCase();
  const match = (r: LayerRow) => !q || r.title.toLowerCase().includes(q) || r.id.toLowerCase().includes(q);

  const active = activeIds.map((id) => rows.find((r) => r.id === id)).filter((r): r is LayerRow => Boolean(r));
  const rest = rows.filter((r) => !activeIds.includes(r.id) && match(r));
  const groups = [...new Set(rest.map((r) => r.group))].map((g) => ({ g, items: rest.filter((r) => r.group === g) }));

  const Row = ({ r, on }: { r: LayerRow; on: boolean }) => (
    <div className={`group flex items-center gap-2 rounded px-1.5 py-1 hover:bg-muted ${r.id === openId ? "bg-muted" : ""}`}>
      <button type="button" onClick={() => onToggle(r.id)} aria-pressed={on}
        className="flex min-w-0 flex-1 items-center gap-2 text-left">
        <span className="h-3 w-3 shrink-0 rounded-sm border border-muted-foreground/50"
          style={on ? { background: colorOf(r.id), borderColor: colorOf(r.id) } : undefined} />
        <span className="truncate" title={r.title}>{r.title}</span>
      </button>
      <button type="button" onClick={() => onOpen(r.href)} title="Details"
        className="shrink-0 rounded px-1 text-muted-foreground opacity-0 hover:text-foreground focus:opacity-100 group-hover:opacity-100">
        ⓘ
      </button>
    </div>
  );

  return (
    <div className="flex flex-col gap-2">
      <input
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
        placeholder="Filter layers…"
        aria-label="Filter layers"
        className="w-full rounded-md border border-border bg-card px-2 py-1.5 text-[13px] outline-none focus:border-primary"
      />

      {active.length > 0 && (
        <div>
          <div className="px-1.5 pb-0.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
            On the map · {active.length}
          </div>
          {active.map((r) => <Row key={r.id} r={r} on />)}
          {legend}
        </div>
      )}

      {groups.map(({ g, items }) => (
        <div key={g}>
          {/* Sticky so the group you're scrolling through stays named. */}
          <div className="sticky top-0 z-10 bg-background px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
            {g}
          </div>
          {items.map((r) => <Row key={r.id} r={r} on={false} />)}
        </div>
      ))}

      {!groups.length && !active.length && (
        <p className="px-1.5 text-muted-foreground">{q ? `Nothing matches “${filter}”.` : "No mappable layers loaded."}</p>
      )}
    </div>
  );
}
