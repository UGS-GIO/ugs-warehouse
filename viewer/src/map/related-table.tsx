// The rows of one related (aspatial child) table for a clicked feature, joined child.field = the
// feature's key value. Rendered in the map Info dock/sheet when a popup affordance is opened — so
// nothing runs until OPEN. Renders the shared DataExplorer (search/sort/paging), filtered to the
// clicked feature — one related-rows surface everywhere, not a separate compact table here.
//
// The join columns live on the FULL item, not the compact index the popup named the table from (the
// index strips ugs:foreign_keys by design — core/stac.py). So opening follows the item's `self`:
// fetch the full item (cached, shared with item-detail), resolve the join, then hand the child
// table's href + an exact-match preset to DataExplorer.
import { useEffect, useState } from "react";

import { DataExplorer } from "@/data/data-explorer";
import type { ColFilter } from "@/data/download";
import { relatedJoins, useStac } from "@/stac";

export function RelatedTable({ itemHref, relatedKey, title, props, onClose }: {
  itemHref: string;                 // the clicked layer's full item (carries the FK spec)
  relatedKey: string;               // which related asset to open
  title: string;
  props: Record<string, unknown>;   // the clicked feature's tile properties — the join-value source
  onClose: () => void;
}) {
  // "Ignore the feature preset" escape hatch: clearing the explorer's preset chip widens to the
  // whole related table without leaving this view. The ← button below stays the primary way back
  // to item detail. Named distinctly from DataExplorer's own `showAll` (which means "one page with
  // every row" — an unrelated axis).
  const [ignorePreset, setIgnorePreset] = useState(false);

  const item = useStac(itemHref);
  const join = relatedJoins(item.data).find((j) => j.key === relatedKey);
  const rawValue = join ? props[join.parentField] : undefined;
  const value = rawValue == null || rawValue === "" ? undefined : String(rawValue);

  // The call site keys `RelatedTable` per layer/related-table, not per clicked feature (desktop
  // keeps the map + dock live across feature clicks) — so a genuinely different feature/table must
  // re-scope explicitly rather than inheriting a stale "ignore preset" from the previous one.
  useEffect(() => setIgnorePreset(false), [join?.href, value]);

  const err = item.error ? "Couldn’t load the layer’s metadata." : undefined;
  const busy = item.isLoading;
  // Item loaded but the feature can't be joined (no such FK / no key value on the feature): nothing
  // to scope the query to.
  const noJoin = !busy && !err && (!join || value === undefined);
  const preset: ColFilter | undefined = join && value !== undefined && !ignorePreset
    ? { col: join.childField, kind: "exact", value }
    : undefined;

  return (
    <div className="flex min-h-0 flex-col gap-2">
      <div className="flex items-center gap-2">
        <button type="button" onClick={onClose} aria-label="Back to item detail" title="Back to item detail"
          className="shrink-0 rounded px-1 text-muted-foreground hover:text-foreground"><span aria-hidden>←</span></button>
        <h3 className="min-w-0 flex-1 truncate text-sm font-semibold text-foreground" title={title}>{title}</h3>
      </div>

      {busy && <p className="px-1 text-sm text-muted-foreground">Loading related rows…</p>}
      {err && <p className="rounded border border-destructive/40 bg-destructive/10 px-2 py-1 text-xs text-destructive">{err}</p>}
      {noJoin && <p className="px-1 text-sm text-muted-foreground">No related rows for this feature.</p>}

      {!err && join && value !== undefined && (
        <DataExplorer key={`${join.href}:${value}`} href={join.href} fill
          presetFilter={preset} onClearPreset={() => setIgnorePreset(true)} />
      )}
    </div>
  );
}
