import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { type ColumnDef, flexRender, getCoreRowModel, type SortingState, useReactTable } from "@tanstack/react-table";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useEffect, useMemo, useRef, useState } from "react";
import { useDebounced } from "@/lib/use-debounced";
import { usePerItem } from "@/lib/use-per-item";

import { CommentsPanel } from "@/review/comments-panel";
import type { ColFilter, ColType } from "./download";
import { PAGE_SIZES } from "./paging";
import type { FocusSel } from "@/map/map-model";
import { IS_REVIEW } from "@/stac";
import { C } from "@/ui/ui";
import { RecordCards } from "@/catalog/record-cards";
import { UiSegmented } from "@/ui/segmented";
import { UiSelect } from "@/ui/select";
import { useIsDesktop } from "@/ui/use-breakpoint";


// Full dataset explorer — the whole GeoParquet, paged/sorted/searched in the browser via
// DuckDB-WASM (HTTP range reads; never downloads the whole file). Server-style manual paging:
// the page query carries LIMIT/OFFSET/ORDER BY/WHERE, so this scales to the 7000-row tables.
// Geometry is excluded (use Download / OGC API / the map for geometry).
// Page sizes come from ./paging, shared with the catalog item lists so both pagers offer the
// same choices. This one opens at the smallest: a row here is a full data record, not a title.
const PAGE_SIZE = PAGE_SIZES[0];
// "All" fetches up to this many rows in one page (the largest tables are ~7k); rows are virtualized
// so only the visible window renders. Capped so a pathological table can't OOM the tab.
const ALL_CAP = 100_000;
// Typed per-column inputs → SQL-ready filters: numeric → range, anything else → substring.
function buildFilters(draft: Record<string, { min?: string; max?: string; text?: string }>,
                      types: Record<string, ColType> | undefined): ColFilter[] {
  const filters: ColFilter[] = [];
  for (const [col, d] of Object.entries(draft)) {
    const kind = types?.[col] ?? "text";
    if (kind === "number") {
      const min = d.min?.trim() ? Number(d.min) : undefined;
      const max = d.max?.trim() ? Number(d.max) : undefined;
      if (Number.isFinite(min) || Number.isFinite(max)) filters.push({ col, kind, min, max });
    } else if (d.text?.trim()) {
      filters.push({ col, kind: "text", contains: d.text });
    }
  }
  return filters;
}

export function DataExplorer({ href, onPick, mapPick, reviewItemId, rowKey = "pk", summaryFields, presetFilter, onClearPreset, fill }: {
  href: string; onPick?: (sel: FocusSel) => void;
  mapPick?: { id: number; nonce: number } | null;
  reviewItemId?: string;  // review deploy: enables per-row + multi-select row comments
  rowKey?: string;        // the stable-key column (e.g. 'pk') a row comment is keyed on
  summaryFields?: readonly string[];   // item's `ugs:summary_fields` — leads the record cards
  presetFilter?: ColFilter;  // exact-match filter ANDed ahead of the user's own filters (e.g. clicked feature's FK)
  onClearPreset?: () => void;  // clears presetFilter — wired to the chip's ✕
  fill?: boolean;  // fill the parent's height (docked contexts) instead of the fixed h-112
}) {
  const review = Boolean(IS_REVIEW && reviewItemId);
  // Row comments: selected STABLE-key values (the pk column), tracked as a Set of string values — not
  // TanStack row-selection (server-paged, row ids reset each page) and not feature_id (ephemeral,
  // differs from the map viewer). A pk resolves to the same row across apps.
  const [selPks, setSelPks] = useState<Set<string>>(new Set());
  const [composeOpen, setComposeOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  // Callers key this component by href, so a new dataset arrives as a fresh mount — no reset effect.
  const togglePk = (pk: string) => setSelPks((prev) => {
    const next = new Set(prev);
    if (next.has(pk)) next.delete(pk); else next.add(pk);
    return next;
  });
  const [pageSize, setPageSize] = useState<number>(PAGE_SIZE);
  const [showAll, setShowAll] = useState(false);  // "All" rows in one virtualized page
  const [sorting, setSorting] = useState<SortingState>([]);
  const desktop = useIsDesktop();
  // Cards are the phone default, but the grid is sometimes the point — comparing a column down the
  // rows. Keep the escape hatch rather than deciding for everyone. Desktop is always the table.
  const [narrowView, setNarrowView] = useState<"cards" | "table">("cards");
  const asCards = !desktop && narrowView === "cards";
  const scrollRef = useRef<HTMLDivElement>(null);  // virtualizer scroll viewport (the resizable box)
  const [search, setSearch] = useState("");
  // feature_id of the row picked from the map (or a table click) — highlighted in the table.
  const [highlightId, setHighlightId] = useState<number | null>(null);
  const [draft, setDraft] = useState<Record<string, { min?: string; max?: string; text?: string }>>({});
  const [colFilters, setColFilters] = useState(false);
  const settledSearch = useDebounced(search);
  const settledDraft = useDebounced(draft);
  // From the schema, not a page: a page is fetched WITH these filters, so that would be circular.
  const { data: types } = useQuery({
    queryKey: ["parquet-types", href],
    queryFn: async () => (await import("./download")).columnTypes(href),
    staleTime: Infinity,
  });
  const applied = useMemo(
    () => ({ search: settledSearch, filters: buildFilters(settledDraft, types) }),
    [settledSearch, settledDraft, types],
  );

  const sort = sorting[0];
  const filterKey = JSON.stringify(applied.filters);
  const presetKey = JSON.stringify(presetFilter);
  // Scoped to what the rows are OF, so a change reads back as page 0 in the same render — one fetch.
  const [pageIndex, setPageIndex] = usePerItem(`${presetKey}|${filterKey}|${applied.search}`, 0);
  // The query key IS the dependency list, so a stale response can no longer land after a newer one
  // (what the `live` flag was guarding by hand). `placeholderData` keeps the previous page on
  // screen while the next one loads, so paging does not blank the table between fetches.
  const { data: page, error, isFetching: loading } = useQuery({
    queryKey: ["parquet-page", href, pageIndex, pageSize, showAll,
               sort?.id, sort?.desc, applied.search, filterKey, presetKey],
    queryFn: async () => {
      const { queryParquet } = await import("./download");
      return queryParquet(href, {
        limit: showAll ? ALL_CAP : pageSize, offset: showAll ? 0 : pageIndex * pageSize,
        orderBy: sort?.id, desc: sort?.desc, search: applied.search,
        filters: presetFilter ? [presetFilter, ...applied.filters] : applied.filters,
      });
    },
    placeholderData: keepPreviousData,   // paging back is served from cache
  });
  const err = error ? (error instanceof Error ? error.message : String(error)) : undefined;


  const columns = useMemo<ColumnDef<Record<string, unknown>, unknown>[]>(
    () => (page?.columns ?? []).map((c) => ({
      id: c, header: c, accessorFn: (row) => row[c],
      cell: (info) => {
        const v = info.getValue();
        const s = v == null ? "" : String(v);
        return <span className="block max-w-[280px] truncate" title={s}>{s}</span>;
      },
    })),
    [page?.columns],
  );

  // Server-side sort/page: TanStack renders + drives the sort UI only (manualSorting), the SQL
  // does the work. Resetting to page 1 on a sort change keeps offset valid.
  const table = useReactTable({
    data: page?.rows ?? [], columns, state: { sorting },
    manualSorting: true, onSortingChange: (u) => { setSorting(u); setPageIndex(0); },
    getCoreRowModel: getCoreRowModel(),
  });

  const total = page?.total ?? 0;
  const pageCount = showAll ? 1 : Math.max(1, Math.ceil(total / pageSize));

  // Virtualize the rows so "All" (up to ~7k) renders only the visible window. Works for paged views
  // too (small counts → negligible overhead). Scroll viewport = the resizable box (scrollRef).
  const rowModel = table.getRowModel().rows;
  const rowVirt = useVirtualizer({
    count: rowModel.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => 29,   // single-line truncated rows; uniform enough to skip per-row measure
    overscan: 16,
  });
  const vItems = rowVirt.getVirtualItems();
  const padTop = vItems.length ? vItems[0].start : 0;
  const padBottom = vItems.length ? rowVirt.getTotalSize() - vItems[vItems.length - 1].end : 0;
  const colCount = (page?.columns?.length ?? 1) + (review ? 1 : 0);
  const btn = "rounded border border-border bg-card px-2 py-0.5 text-xs text-foreground hover:border-primary disabled:opacity-40";
  const fIn = "w-full min-w-16 rounded border border-input bg-card px-1 py-0.5 text-xs font-normal normal-case text-foreground";
  const hasFilters = Boolean(search) || applied.filters.length > 0
    || Object.values(draft).some((d) => d.min || d.max || d.text);
  // Per-column filters are off by default: one input under every one of 29 columns dominated the
  // page visually while being used rarely. Stays open once opened, and forced open while a filter
  // is active so you can always see (and clear) what is narrowing the rows.
  const showColFilters = colFilters || applied.filters.length > 0;
  const clearAll = () => { setSearch(""); setDraft({}); };

  // Row-comment selection helpers (review deploy). The pk column is the stable per-row handle.
  const pagePks = (page?.rows ?? []).map((r) => r[rowKey]).filter((v) => v != null).map(String);
  const pageAllSelected = pagePks.length > 0 && pagePks.every((p) => selPks.has(p));
  const pageSomeSelected = pagePks.some((p) => selPks.has(p));
  const selArr = [...selPks];

  // Row click → zoom + highlight. Fire the bbox immediately (instant feedback), then fetch the
  // real geometry (same filter+sort, offset = page start + row index) and upgrade the highlight.
  const pick = (i: number, bbox: [number, number, number, number]) => {
    if (!onPick) return;
    const offset = pageIndex * pageSize + i;
    const key = `row:${offset}`;          // same key for both onPick calls → one fly per click
    onPick({ bbox, key });
    import("./download").then(({ fetchGeometry }) => fetchGeometry(href,
      { orderBy: sort?.id, desc: sort?.desc, search: applied.search, filters: applied.filters }, offset))
      .then((g) => { if (g) onPick({ bbox, geometry: g, key }); })
      .catch(() => {});
  };

  // Map-feature click → highlight + fly to the real feature (looked up by id, independent of the
  // current filter) AND page the table to it under the current sort/filter. Paging is skipped if
  // the feature is filtered out of the visible set (ordinal null); the highlight + fly still fire.
  // Depends only on the click nonce, so it captures the sort/filter as of the click — re-running on
  // every filter keystroke would yank the page around.
  useEffect(() => {
    if (!mapPick) return;
    let live = true;
    setHighlightId(mapPick.id);
    const key = `map:${mapPick.nonce}`;   // unique per map click → always re-flies
    (async () => {
      const { fetchRowById, ordinalByFeatureId } = await import("./download");
      const row = await fetchRowById(href, mapPick.id);
      if (live && row && onPick) onPick({ bbox: row.bbox, geometry: row.geometry, key });
      const pos = await ordinalByFeatureId(href, mapPick.id, {
        orderBy: sort?.id, desc: sort?.desc, search: applied.search, filters: applied.filters,
      });
      if (live && pos != null) setPageIndex(Math.floor(pos / pageSize));
    })();
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mapPick?.nonce]);

  return (
    <div className={fill ? "mt-2 flex min-h-0 flex-1 flex-col" : "mt-2"}>
      {/* One header line that says what this is and how big it is — the row count used to float
          mid-toolbar and the disclosure was a bare chevron on its own line. */}
      <div className="mb-1.5 flex flex-wrap items-center gap-2">
        <button className="inline-flex items-baseline gap-1.5 text-xs font-medium text-muted-foreground hover:text-foreground"
          title={collapsed ? "Show the data" : "Hide the data"} aria-expanded={!collapsed}
          onClick={() => setCollapsed((v) => !v)}>
          <span aria-hidden>{collapsed ? "▸" : "▾"}</span>
          Data
          <span className="font-normal">
            · {page ? `${total.toLocaleString()} row${total === 1 ? "" : "s"}` : "…"}{loading ? " · loading" : ""}
          </span>
        </button>
        {!collapsed && (
          <>
            {!desktop && (
              <UiSegmented className="ml-auto" value={narrowView} onValueChange={setNarrowView}
                items={[{ value: "cards", label: "Cards" }, { value: "table", label: "Table" }] as const} />
            )}
            <button className={`rounded border border-border px-2 py-0.5 text-xs text-muted-foreground hover:border-primary ${desktop ? "ml-auto" : ""}`}
              aria-pressed={showColFilters} onClick={() => setColFilters((v) => !v)}>
              {showColFilters ? "Hide column filters" : "Filter columns"}
            </button>
            {hasFilters && <button className="text-xs text-primary" onClick={clearAll}>clear filters</button>}
            {presetFilter && (
              <span className="inline-flex items-center gap-1 rounded border border-primary/40 bg-primary/10 px-2 py-0.5 text-xs text-primary">
                Showing rows for the clicked feature
                <button type="button" onClick={onClearPreset} aria-label="Clear feature filter" className="hover:opacity-80">✕</button>
              </span>
            )}
          </>
        )}
      </div>
      {!collapsed && (
        <div className="mb-1.5 flex flex-wrap items-center gap-2">
          <input className={`${C.input} min-w-48 flex-1`} placeholder="Search all columns…" value={search}
            aria-label="Search all columns" onChange={(e) => setSearch(e.target.value)} />
          {onPick && page?.bboxes.some(Boolean) && (
            <span className={C.muted}>click a row to zoom</span>
          )}
        </div>
      )}
      {err && <div className="mb-1.5 text-xs text-destructive">explorer failed: {err}</div>}
      {review && selPks.size > 0 && (
        <div className="mb-1.5 flex items-center gap-2 rounded-md border border-amber-500/40 bg-amber-500/10 px-2 py-1 text-xs">
          <span className="font-medium text-amber-700 dark:text-amber-400">{selPks.size} row{selPks.size === 1 ? "" : "s"} selected</span>
          <button className="rounded border border-amber-500/50 bg-amber-500/15 px-2 py-0.5 font-medium text-amber-700 hover:bg-amber-500/25 dark:text-amber-300"
            onClick={() => setComposeOpen(true)}>💬 Comment on {selPks.size === 1 ? "row" : `${selPks.size} rows`}</button>
          <button className="text-muted-foreground hover:underline" onClick={() => { setSelPks(new Set()); setComposeOpen(false); }}>clear</button>
        </div>
      )}
      {/* Phones get the same rows as cards: a 22-column table is ~2400px wide, which on a 390px
          screen is a sideways-scrolling box inside a scrolling page. */}
      {asCards && !collapsed && (
        <RecordCards
          rows={rowModel}
          summaryFields={summaryFields}
          highlight={(r) => {
            const fid = r.original.feature_id;
            return fid != null && Number(fid) === highlightId;
          }}
          onPick={onPick ? (r) => {
            const bbox = page?.bboxes[r.index] ?? null;
            if (!bbox) return;
            pick(r.index, bbox);
            const fid = r.original.feature_id;
            if (fid != null) setHighlightId(Number(fid));
          } : undefined}
        />
      )}
      <div ref={scrollRef} className={`max-w-full overflow-auto rounded-md border border-border text-xs ${collapsed || asCards ? "hidden" : fill ? "h-full min-h-40" : "h-112 min-h-40 resize-y"}`}>
        <table className="w-auto min-w-full border-collapse">
          <thead className="sticky top-0 z-10 bg-card">
            {table.getHeaderGroups().map((hg) => (
              <tr key={hg.id}>
                {review && (
                  <th className={`${C.th} w-8 text-center`} title="Select rows to comment on">
                    <input type="checkbox" aria-label="Select all rows on this page"
                      checked={pageAllSelected}
                      ref={(el) => { if (el) el.indeterminate = pageSomeSelected && !pageAllSelected; }}
                      onChange={() => setSelPks((prev) => {
                        const next = new Set(prev);
                        if (pageAllSelected) pagePks.forEach((p) => next.delete(p));
                        else pagePks.forEach((p) => next.add(p));
                        return next;
                      })} />
                  </th>
                )}
                {hg.headers.map((h) => {
                  const s = h.column.getIsSorted();
                  return (
                    <th key={h.id} className={C.th} onClick={h.column.getToggleSortingHandler()}>
                      {flexRender(h.column.columnDef.header, h.getContext())}
                      {s === "asc" ? " ▲" : s === "desc" ? " ▼" : ""}
                    </th>
                  );
                })}
              </tr>
            ))}
            {/* Per-column filter row: numeric → min/max range, text → substring. */}
            <tr className={showColFilters ? "" : "hidden"}>
              {review && <th className="border-b border-border" />}
              {(page?.columns ?? []).map((col) => {
                const kind = types?.[col] ?? "text";
                const d = draft[col] ?? {};
                const set = (patch: Partial<{ min: string; max: string; text: string }>) =>
                  setDraft((prev) => ({ ...prev, [col]: { ...prev[col], ...patch } }));
                return (
                  <th key={col} className="border-b border-border px-1.5 py-1 align-top">
                    {/* Labelled per column — every placeholder here reads "contains…". */}
                    {kind === "number" ? (
                      <div className="flex gap-1">
                        <input className={fIn} placeholder="min" value={d.min ?? ""} type="number"
                          aria-label={`${col} minimum`} onChange={(e) => set({ min: e.target.value })} />
                        <input className={fIn} placeholder="max" value={d.max ?? ""} type="number"
                          aria-label={`${col} maximum`} onChange={(e) => set({ max: e.target.value })} />
                      </div>
                    ) : (
                      <input className={fIn} placeholder="contains…" value={d.text ?? ""}
                        aria-label={`${col} contains`} onChange={(e) => set({ text: e.target.value })} />
                    )}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {/* Virtualized window: only the visible rows are in the DOM; spacer rows hold the scroll
                height above/below so "All" (up to ~7k rows) stays smooth. */}
            {padTop > 0 && <tr aria-hidden style={{ height: padTop }}><td colSpan={colCount} /></tr>}
            {vItems.map((vi) => {
              const r = rowModel[vi.index];
              const bbox = page?.bboxes[r.index] ?? null;
              const clickable = Boolean(onPick && bbox);
              const fid = r.original.feature_id;
              const nfid = fid != null ? Number(fid) : null;
              const pkRaw = r.original[rowKey];
              const pkStr = pkRaw != null ? String(pkRaw) : null;
              const hl = nfid != null && nfid === highlightId;
              return (
                <tr key={r.id} data-index={vi.index} ref={rowVirt.measureElement}
                  className={`${hl ? "bg-amber-100 dark:bg-amber-900/40" : ""} ${clickable ? "cursor-pointer hover:bg-hover" : ""}`.trim() || undefined}
                  title={clickable ? "Zoom to feature on map" : undefined}
                  onClick={clickable ? () => { pick(r.index, bbox!); if (nfid != null) setHighlightId(nfid); } : undefined}>
                  {review && (
                    <td className="w-8 px-1 text-center align-middle" onClick={(e) => e.stopPropagation()}>
                      {pkStr != null ? (
                        <div className="flex items-center gap-1">
                          <input type="checkbox" aria-label={`Select ${rowKey} ${pkStr}`}
                            checked={selPks.has(pkStr)} onChange={() => togglePk(pkStr)} />
                          <button title="Comment on this row" className="text-xs hover:opacity-70"
                            onClick={() => { setSelPks(new Set([pkStr])); setComposeOpen(true); }}>💬</button>
                        </div>
                      ) : null}
                    </td>
                  )}
                  {r.getVisibleCells().map((c) => (
                    <td key={c.id} className={C.td}>{flexRender(c.column.columnDef.cell, c.getContext())}</td>
                  ))}
                </tr>
              );
            })}
            {padBottom > 0 && <tr aria-hidden style={{ height: padBottom }}><td colSpan={colCount} /></tr>}
            {!loading && total === 0 && (
              <tr><td className="px-2.5 py-2 text-muted-foreground" colSpan={colCount}>No rows match.</td></tr>
            )}
          </tbody>
        </table>
      </div>
      <div className={`mt-1.5 flex flex-wrap items-center gap-1.5 text-xs ${collapsed ? "hidden" : ""}`}>
        <button className={btn} disabled={pageIndex === 0} onClick={() => setPageIndex(0)}>«</button>
        <button className={btn} disabled={pageIndex === 0} onClick={() => setPageIndex((i) => i - 1)}>‹ Prev</button>
        <span className="px-1 text-muted-foreground">Page {pageIndex + 1} of {pageCount}</span>
        <button className={btn} disabled={pageIndex + 1 >= pageCount} onClick={() => setPageIndex((i) => i + 1)}>Next ›</button>
        <button className={btn} disabled={pageIndex + 1 >= pageCount} onClick={() => setPageIndex(pageCount - 1)}>»</button>
        <label className="ml-1 flex items-center gap-1 text-muted-foreground">
          Rows
          <UiSelect className="px-1 py-0.5"
            value={showAll ? "all" : String(pageSize)}
            onValueChange={(v) => {
              setPageIndex(0);
              if (v === "all") setShowAll(true);
              else { setShowAll(false); setPageSize(Number(v)); }
            }}
            items={[...PAGE_SIZES.map((n) => ({ value: String(n), label: String(n) })),
                    { value: "all", label: "All" }]} />
        </label>
        <label className="flex items-center gap-1 text-muted-foreground">
          Go to
          <input type="number" min={1} max={pageCount}
            className="w-16 rounded border border-border bg-card px-1 py-0.5 text-foreground"
            value={pageIndex + 1}
            onChange={(e) => {
              const n = Number(e.target.value);
              if (Number.isFinite(n)) setPageIndex(Math.min(pageCount, Math.max(1, n)) - 1);
            }} />
        </label>
        {total > 0 && (
          <span className="ml-auto text-muted-foreground">
            {showAll
              ? `1–${rowModel.length.toLocaleString()}${rowModel.length < total ? ` (capped at ${ALL_CAP.toLocaleString()})` : ""}`
              : `${(pageIndex * pageSize + 1).toLocaleString()}–${Math.min((pageIndex + 1) * pageSize, total).toLocaleString()}`}
            {" "}of {total.toLocaleString()}
          </span>
        )}
      </div>
      {review && composeOpen && selPks.size > 0 && (
        <div className="mt-2 rounded-md border border-amber-500/40 bg-amber-500/[0.04] p-3">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold">
              {selArr.length > 1 ? `Comment on ${selArr.length} rows` : `${rowKey} ${selArr[0]}`}
            </h3>
            <button className="text-xs text-muted-foreground hover:underline" onClick={() => setComposeOpen(false)}>close</button>
          </div>
          <CommentsPanel itemId={reviewItemId!}
            target={{ kind: "row", rowKey, rowVals: selArr, rowVal: selArr.length === 1 ? selArr[0] : undefined }}
            label={selArr.length > 1 ? `New note on ${selArr.length} rows` : `Comments on ${rowKey} ${selArr[0]}`} />
        </div>
      )}
    </div>
  );
}

// ---- asset viewer: peruse a publication's files in-page (PDF / image / COG / parquet / text) ----
