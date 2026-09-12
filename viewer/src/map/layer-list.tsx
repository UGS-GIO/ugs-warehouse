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

// Two kinds of thing, and mart schema / publication series only groups WITHIN one of them. Without
// this level the plates bury the topics — one series alone runs to 28 rows.
const PLATES = "Published maps";
const SECTIONS = [
  { key: "Data layers", layer: true },
  { key: PLATES, layer: false },
];

/** One collapsible level. Open/closed lives in the caller's set so both levels share one store. */
function Fold({ id, label, action, indent, forceOpen, closed, setClosed, children }: {
  id: string; label: ReactNode; action?: ReactNode; indent?: boolean; forceOpen: boolean;
  closed: Set<string>; setClosed: (fn: (prev: Set<string>) => Set<string>) => void;
  children: ReactNode;
}) {
  return (
    <details open={forceOpen || !closed.has(id)} className={indent ? "ml-2" : undefined}
      onToggle={(e) => {
        const { open } = e.currentTarget;   // read before the updater runs — React nulls it
        setClosed((prev) => {
          const next = new Set(prev);
          if (open) next.delete(id); else next.add(id);
          return next;
        });
      }}>
      <summary className="sticky top-0 z-10 flex cursor-pointer list-none items-center bg-background px-1.5 py-0.5 text-sm font-semibold uppercase tracking-wider text-muted-foreground hover:text-foreground">
        <span className="inline-block w-3 shrink-0 transition-transform [details[open]>summary_&]:rotate-90">▸</span>
        <span className="min-w-0 truncate">{label}</span>
        {action && <span className="ml-auto pl-2">{action}</span>}
      </summary>
      {children}
    </details>
  );
}

const BULK_CLASS = "cursor-pointer rounded border border-border px-1.5 py-0.5 text-[11px] font-medium normal-case tracking-normal hover:bg-hover";

export function LayerList({ rows, activeIds, colorOf, onToggle, onToggleMany, onOpen, openId, legend }: {
  rows: LayerRow[];
  activeIds: string[];
  colorOf: (id: string) => string | undefined;
  onToggle: (id: string) => void;
  // Whole group on/off — a publication series or a mart schema is what people actually want drawn.
  onToggleMany: (ids: string[], on: boolean) => void;
  onOpen: (href: string) => void;
  openId?: string;
  legend?: ReactNode;   // what the active layers mean — belongs with them, not after the whole list
}) {
  const [filter, setFilter] = useState("");
  // Published maps start closed: they outnumber the serving topics several times over, and someone
  // opening the map view is nearly always after a data layer.
  const [closed, setClosed] = useState<Set<string>>(() => new Set([PLATES]));
  const q = filter.trim().toLowerCase();
  const match = (r: LayerRow) => !q || r.title.toLowerCase().includes(q) || r.id.toLowerCase().includes(q);

  const active = activeIds.map((id) => rows.find((r) => r.id === id)).filter((r): r is LayerRow => Boolean(r));
  const rest = rows.filter((r) => !activeIds.includes(r.id) && match(r));
  const groupsOf = (items: LayerRow[]) =>
    [...new Set(items.map((r) => r.group))].map((g) => ({ g, items: items.filter((r) => r.group === g) }));
  const sections = SECTIONS
    .map((s) => ({ ...s, items: rest.filter((r) => r.layer === s.layer) }))
    .filter((s) => s.items.length);

  const Row = ({ r, on }: { r: LayerRow; on: boolean }) => (
    <div className={`group flex items-center gap-2 rounded px-1.5 py-1 hover:bg-hover ${r.id === openId ? "bg-muted" : ""}`}>
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
        className="w-full rounded-md border border-border bg-card px-2 py-1.5 text-sm outline-none focus:border-primary"
      />

      {active.length > 0 && (
        <div>
          <div className="flex items-center px-1.5 pb-0.5 text-sm font-semibold uppercase tracking-wider text-muted-foreground">
            On the map · {active.length}
            {/* Clear the FULL active set (activeIds), not just `active` — the resolved subset. An id
                in ?l= whose collection index hasn't loaded yet isn't in `active`, so clearing that
                would leave it behind; a shared or deep-linked set could never be fully cleared. */}
            <button type="button" className={`${BULK_CLASS} ml-auto`} title="Turn every layer off"
              onClick={() => onToggleMany(activeIds, false)}>
              Clear
            </button>
          </div>
          {active.map((r) => <Row key={r.id} r={r} on />)}
          {legend}
        </div>
      )}

      {/* Collapsible, two deep: kind, then mart schema / publication series. A filter force-opens
          everything, since a closed group would hide its own matches. */}
      {sections.map((s) => (
        <Fold key={s.key} id={s.key} closed={closed} setClosed={setClosed} forceOpen={Boolean(q)}
          label={<>{s.key} <span className="font-normal normal-case">· {s.items.length}</span></>}>
          {groupsOf(s.items).map(({ g, items }) => (
            <Fold key={g} id={`${s.key}/${g}`} closed={closed} setClosed={setClosed} forceOpen={Boolean(q)}
              label={<>{g} <span className="font-normal normal-case">· {items.length}</span></>} indent
              // The group's own rows only: what's already on sits in the pinned set above, so
              // "All" here never re-adds it and never silently turns anything off.
              action={
                <button type="button" className={BULK_CLASS}
                  title={`Draw all ${items.length} layers in ${g}`}
                  onClick={(e) => { e.preventDefault(); onToggleMany(items.map((r) => r.id), true); }}>
                  All
                </button>
              }>
              {items.map((r) => <Row key={r.id} r={r} on={false} />)}
            </Fold>
          ))}
        </Fold>
      ))}

      {!sections.length && !active.length && (
        <p className="px-1.5 text-muted-foreground">{q ? `Nothing matches “${filter}”.` : "No mappable layers loaded."}</p>
      )}
    </div>
  );
}
