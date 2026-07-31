import { flexRender, type Table } from "@tanstack/react-table";

import { ALL_PAGES, pageLabel, PAGE_SIZES, type PageSize } from "./paging";
import { C } from "./ui";

// Shared class names and table chrome — the item list and the data explorer both render these.

const pageBtn = (disabled: boolean) =>
  `border border-border px-2 py-1 text-xs ${disabled
    ? "cursor-default bg-card text-muted-foreground/40"
    : "cursor-pointer bg-card text-foreground hover:bg-accent"}`;

/** Page-size chooser + first/prev/next/last, driven by the table's own pagination state. */
export function Pager<T>({ table, size, onSize }: {
  table: Table<T>; size: PageSize; onSize: (s: PageSize) => void;
}) {
  const index = table.getState().pagination.pageIndex;
  const pages = table.getPageCount();
  const back = !table.getCanPreviousPage();
  const fwd = !table.getCanNextPage();
  return (
    <div className="my-2 flex flex-wrap items-center gap-2.5">
      <span className={C.muted}>{pageLabel(index, table.getRowCount(), size)}</span>
      <span className="flex items-center gap-1">
        <button className={pageBtn(back)} disabled={back} title="First page"
          onClick={() => table.firstPage()}>«</button>
        <button className={pageBtn(back)} disabled={back} title="Previous page"
          onClick={() => table.previousPage()}>‹</button>
        <span className="px-1 text-xs text-muted-foreground">
          {size === ALL_PAGES ? "All items" : `Page ${index + 1} of ${pages}`}
        </span>
        <button className={pageBtn(fwd)} disabled={fwd} title="Next page"
          onClick={() => table.nextPage()}>›</button>
        <button className={pageBtn(fwd)} disabled={fwd} title="Last page"
          onClick={() => table.lastPage()}>»</button>
      </span>
      <label className="flex items-center gap-1 text-xs text-muted-foreground">
        <span>Per page</span>
        <select className="rounded border border-border bg-background px-1 py-0.5 text-xs text-foreground"
          value={String(size)}
          onChange={(e) => onSize(e.target.value === ALL_PAGES ? ALL_PAGES : Number(e.target.value))}>
          {PAGE_SIZES.map((n) => <option key={n} value={n}>{n}</option>)}
          <option value={ALL_PAGES}>All</option>
        </select>
      </label>
    </div>
  );
}

export function DataTable<T>({ table, onRowClick }: {
  table: Table<T>;
  onRowClick?: (row: T) => void;
}) {
  return (
    <table className="w-full border-collapse">
      <thead>
        {table.getHeaderGroups().map((hg) => (
          <tr key={hg.id}>
            {hg.headers.map((h) => {
              const s = h.column.getIsSorted();
              return (
                <th key={h.id} className={h.column.getCanSort() ? C.th : C.thPlain}
                  onClick={h.column.getToggleSortingHandler()}>
                  {flexRender(h.column.columnDef.header, h.getContext())}
                  {s === "asc" ? " ▲" : s === "desc" ? " ▼" : ""}
                </th>
              );
            })}
          </tr>
        ))}
      </thead>
      <tbody>
        {table.getRowModel().rows.map((r) => (
          <tr key={r.id} className={onRowClick ? "cursor-pointer hover:bg-muted" : undefined}
            onClick={onRowClick ? () => onRowClick(r.original) : undefined}>
            {r.getVisibleCells().map((c) => (
              <td key={c.id} className={C.td}>{flexRender(c.column.columnDef.cell, c.getContext())}</td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}


