// The rows of one related (aspatial child) table for a clicked feature, joined child.field = the
// feature's key value. Rendered in the map Info dock/sheet when a popup affordance is opened — so
// nothing runs until OPEN. A compact read-only table (the DataExplorer is the full browse surface;
// this is the focused peek for one feature's related rows).
//
// The join columns live on the FULL item, not the compact index the popup named the table from (the
// index strips ugs:foreign_keys by design — core/stac.py). So opening follows the item's `self`:
// fetch the full item (cached, shared with item-detail), resolve the join, then range-read the rows.
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import type { ColFilter } from "@/data/download";
import { relatedJoins, useStac } from "@/stac";

const PAGE = 25;

export function RelatedTable({ itemHref, relatedKey, title, props, onClose }: {
  itemHref: string;                 // the clicked layer's full item (carries the FK spec)
  relatedKey: string;               // which related asset to open
  title: string;
  props: Record<string, unknown>;   // the clicked feature's tile properties — the join-value source
  onClose: () => void;
}) {
  const [page, setPage] = useState(0);

  const item = useStac(itemHref);
  const join = relatedJoins(item.data).find((j) => j.key === relatedKey);
  const rawValue = join ? props[join.parentField] : undefined;
  const value = rawValue == null || rawValue === "" ? undefined : String(rawValue);
  const canQuery = Boolean(join && value !== undefined);

  const filters: ColFilter[] = canQuery ? [{ col: join!.childField, kind: "exact", value: value! }] : [];
  const { data, error, isFetching } = useQuery({
    queryKey: ["related-rows", join?.href, join?.childField, value, page],
    enabled: canQuery,
    queryFn: async () => {
      const { queryParquet } = await import("@/data/download");
      return queryParquet(join!.href, { limit: PAGE, offset: page * PAGE, filters });
    },
    placeholderData: keepPreviousData,
    staleTime: 30_000,   // the parquet is immutable per ingest — paging back is free
  });

  const err = item.error ? "Couldn’t load the layer’s metadata."
    : error ? (error instanceof Error ? error.message : String(error)) : undefined;
  const busy = item.isLoading || (canQuery && isFetching && !data);
  // Item loaded but the feature can't be joined (no such FK / no key value on the feature), or the
  // query returned nothing: either way there's nothing to show.
  const empty = !busy && !err && (!canQuery || (data?.rows.length ?? 0) === 0);
  const total = data?.total ?? 0;
  const lastPage = Math.max(0, Math.ceil(total / PAGE) - 1);

  return (
    <div className="flex min-h-0 flex-col gap-2">
      <div className="flex items-center gap-2">
        <button type="button" onClick={onClose} aria-label="Back to item detail" title="Back to item detail"
          className="shrink-0 rounded px-1 text-muted-foreground hover:text-foreground"><span aria-hidden>←</span></button>
        <h3 className="min-w-0 flex-1 truncate text-sm font-semibold text-foreground" title={title}>{title}</h3>
        {!busy && !err && canQuery && (
          <span className="shrink-0 text-xs text-muted-foreground">{total.toLocaleString()} {total === 1 ? "row" : "rows"}</span>
        )}
      </div>

      {busy && <p className="px-1 text-sm text-muted-foreground">Loading related rows…</p>}
      {err && <p className="rounded border border-destructive/40 bg-destructive/10 px-2 py-1 text-xs text-destructive">{err}</p>}
      {empty && <p className="px-1 text-sm text-muted-foreground">No related rows for this feature.</p>}

      {!busy && !err && !empty && (
        <div className="min-h-0 flex-1 overflow-auto rounded border border-border">
          <table className="w-full border-collapse text-xs">
            <thead className="sticky top-0 bg-muted">
              <tr>{data!.columns.map((col) => (
                <th key={col} scope="col" className="whitespace-nowrap border-b border-border px-2 py-1 text-left font-semibold text-muted-foreground">{col}</th>
              ))}</tr>
            </thead>
            <tbody>
              {data!.rows.map((row, i) => (
                <tr key={i} className="odd:bg-background even:bg-muted/40">
                  {data!.columns.map((col) => {
                    const v = row[col];
                    const s = v == null ? "" : String(v);
                    return <td key={col} className="max-w-[220px] truncate border-b border-border/60 px-2 py-1" title={s}>{s}</td>;
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {!err && total > PAGE && (
        <div className="flex shrink-0 items-center justify-between text-xs text-muted-foreground">
          <button type="button" disabled={page === 0} onClick={() => setPage((p) => Math.max(0, p - 1))}
            className="rounded border border-border px-2 py-0.5 hover:bg-hover disabled:opacity-40">Prev</button>
          <span>Page {page + 1} of {lastPage + 1}</span>
          <button type="button" disabled={page >= lastPage} onClick={() => setPage((p) => Math.min(lastPage, p + 1))}
            className="rounded border border-border px-2 py-0.5 hover:bg-hover disabled:opacity-40">Next</button>
        </div>
      )}
    </div>
  );
}
