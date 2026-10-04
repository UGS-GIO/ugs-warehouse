// The rows of one related (aspatial child) table for a clicked feature, in the map Info dock —
// the shared DataExplorer filtered to that feature, so there is one related-rows surface everywhere.
//
// The join columns live on the FULL item, not the compact index the table was named from (the index
// strips ugs:foreign_keys by design — core/stac.py), so opening follows the item's `self` and
// resolves the join from there. Nothing fetches until a table is opened.
import { DataExplorer } from "@/data/data-explorer";
import type { ColFilter } from "@/data/download";
import { relatedJoins, useStac } from "@/stac";
import { usePerItem } from "@/lib/use-per-item";

export function RelatedTable({ itemHref, relatedKey, title, props, onClose }: {
  itemHref: string;                 // the clicked layer's full item (carries the FK spec)
  relatedKey: string;               // which related asset to open
  title: string;
  props: Record<string, unknown>;   // the clicked feature's tile properties — the join-value source
  onClose: () => void;
}) {
  const item = useStac(itemHref);
  const join = relatedJoins(item.data).find((j) => j.key === relatedKey);
  const rawValue = join ? props[join.parentField] : undefined;
  const value = rawValue == null || rawValue === "" ? undefined : String(rawValue);

  // "Ignore the feature preset" escape hatch: clearing the chip widens to the whole related table
  // without leaving this view. Not DataExplorer's `showAll` (one page with every row). Scoped by
  // `scopeKey` because the call site keeps this mounted across feature clicks.
  const scopeKey = `${join?.href}:${value}`;
  const [ignorePreset, setIgnorePreset] = usePerItem(scopeKey, false);

  const err = item.error ? "Couldn’t load the layer’s metadata." : undefined;
  const busy = item.isLoading;
  // Item loaded but the feature can't be joined (no such FK / no key value on the feature): nothing
  // to scope the query to.
  const noJoin = !busy && !err && (!join || value === undefined);
  const preset: ColFilter | undefined = join && value !== undefined && !ignorePreset
    ? { col: join.childField, kind: "exact", value }
    : undefined;

  return (
    <div className="flex h-full min-h-0 flex-col gap-2">
      <div className="flex items-center gap-2">
        <button type="button" onClick={onClose} aria-label="Back" title="Back"
          className="shrink-0 rounded px-1 text-muted-foreground hover:text-foreground"><span aria-hidden>←</span></button>
        <h3 className="min-w-0 flex-1 truncate text-sm font-semibold text-foreground" title={title}>{title}</h3>
      </div>

      {busy && <p className="px-1 text-sm text-muted-foreground">Loading related rows…</p>}
      {err && <p className="rounded border border-destructive/40 bg-destructive/10 px-2 py-1 text-xs text-destructive">{err}</p>}
      {noJoin && <p className="px-1 text-sm text-muted-foreground">No related rows for this feature.</p>}

      {!err && join && value !== undefined && (
        <DataExplorer key={scopeKey} href={join.href} fill
          presetFilter={preset} onClearPreset={() => setIgnorePreset(true)} />
      )}
    </div>
  );
}
