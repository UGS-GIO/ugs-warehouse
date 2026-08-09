// The data explorer's rows as stacked records, for phones. A 22-column table is ~2400px wide: on a
// 390px screen that's a sideways-scrolling box inside a vertically-scrolling page, which is the
// worst of both. One card per feature reads top-to-bottom instead.
//
// Renders through the table's own cells (`getVisibleCells` + `flexRender`), so cards inherit the
// column defs, their cell renderers and the column-visibility state rather than re-reading the raw
// row. Cards show the first few fields and open to the rest — `<details>` carries that, no state.
import { type Cell, flexRender, type Row } from "@tanstack/react-table";

import { constantFields, previewOrder } from "./record-fields";

const PREVIEW_FIELDS = 4;   // enough to tell records apart; the rest is one tap away

export type ExplorerRow = Record<string, unknown>;

const headerOf = (cell: Cell<ExplorerRow, unknown>): string => {
  const h = cell.column.columnDef.header;
  return typeof h === "string" ? h : cell.column.id;
};

export function RecordCards({ rows, onPick, highlight }: {
  rows: Row<ExplorerRow>[];
  onPick?: (row: Row<ExplorerRow>) => void;
  highlight?: (row: Row<ExplorerRow>) => boolean;
}) {
  if (!rows.length) return <p className="px-1 py-2 text-muted-foreground">No rows match.</p>;
  // Sampled, not exhaustive: "All rows" can be thousands, and 50 is plenty to spot a constant.
  const named = (r: Row<ExplorerRow>) => r.getVisibleCells().map((c) => ({ name: headerOf(c), value: c.getValue() }));
  const constant = constantFields(rows.slice(0, 50).map(named));
  return (
    <div className="flex flex-col gap-2">
      {rows.map((r) => {
        const cells = r.getVisibleCells();
        // Lead with the fields that identify THIS record, not whichever columns came first.
        const leadIdx = new Set(previewOrder(named(r), PREVIEW_FIELDS, constant));
        const lead = cells.filter((_, i) => leadIdx.has(i));
        const rest = cells.filter((_, i) => !leadIdx.has(i));
        return (
          <details key={r.id}
            className={`rounded-md border px-2.5 py-2 text-[12px] ${highlight?.(r)
              ? "border-amber-500/60 bg-amber-100 dark:bg-amber-900/40" : "border-border bg-card"}`}>
            <summary className="cursor-pointer list-none">
              <dl className="grid grid-cols-[minmax(0,7rem)_1fr] gap-x-3 gap-y-0.5">
                {lead.map((c) => <Field key={c.id} cell={c} />)}
              </dl>
              {rest.length > 0 && (
                <span className="mt-1 inline-block text-[11px] text-primary">{rest.length} more fields</span>
              )}
            </summary>
            <dl className="mt-1.5 grid grid-cols-[minmax(0,7rem)_1fr] gap-x-3 gap-y-0.5 border-t border-border pt-1.5">
              {rest.map((c) => <Field key={c.id} cell={c} />)}
            </dl>
            {onPick && (
              <button type="button" onClick={() => onPick(r)}
                className="mt-2 rounded border border-border px-2 py-1 text-[11px] text-foreground hover:bg-muted">
                Zoom to feature
              </button>
            )}
          </details>
        );
      })}
    </div>
  );
}

function Field({ cell }: { cell: Cell<ExplorerRow, unknown> }) {
  const name = headerOf(cell);
  return (
    <>
      <dt className="truncate text-[10px] uppercase tracking-wide text-muted-foreground" title={name}>{name}</dt>
      <dd className="min-w-0 break-words text-foreground">
        {flexRender(cell.column.columnDef.cell, cell.getContext())}
      </dd>
    </>
  );
}
