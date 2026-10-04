// The clicked feature's identity + a related PEEK. Strict peek: NEVER an embedded rows table — full
// rows live in the Related panel and the /map dock.
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { qk } from "@/query-keys";
import { CommentsPanel } from "@/review/comments-panel";
import { IS_REVIEW, primaryKeyOf, relatedJoins, type RelatedJoin, summaryFieldsOf, type StacDoc } from "@/stac";

// One related child table's peek for the clicked feature: 0 rows → hidden; 1 → the row inline
// (description clamped); many → a hand-off button. Never a table — the full rows live in the panel/dock.
function PeekRow({ join, value, onOpen }: {
  join: RelatedJoin; value: unknown; onOpen?: (relatedKey: string, value: string) => void;
}) {
  const v = value == null || value === "" ? undefined : String(value);
  const { data, isFetching, error } = useQuery({
    enabled: v !== undefined,
    queryKey: qk.featureRelated(join.href, join.childField, v!),
    // An exact match read with hyparquet (parquet-lite.ts): a click should not start DuckDB.
    queryFn: async () => (await import("@/data/parquet-lite")).readMatching(join.href, join.childField, v!, { limit: 6, offset: 0 }),
  });
  if (v === undefined) return null;
  const total = data?.total ?? 0;
  if (isFetching && !data) return <div className="py-0.5 text-xs text-muted-foreground">{join.title}…</div>;
  if (error) return <div className="py-0.5 text-xs text-destructive">{join.title}: couldn't load</div>;
  if (total === 0) return null;
  if (total === 1) {
    const row = data!.rows[0];
    return (
      <div className="py-1">
        <div className="text-xs font-semibold">{join.title}</div>
        {data!.columns.filter((k) => k !== join.childField && row[k] != null && row[k] !== "").slice(0, 6).map((k) => (
          <div key={k} className="text-xs" style={{ display: "-webkit-box", WebkitLineClamp: 3, WebkitBoxOrient: "vertical", overflow: "hidden" }}>
            <span className="text-muted-foreground">{k}:</span> {String(row[k])}
          </div>
        ))}
      </div>
    );
  }
  return (
    <button type="button" onClick={() => onOpen?.(join.key, v)}
      className="block w-full text-left text-xs font-medium text-primary hover:underline">
      {total} related in {join.title} →
    </button>
  );
}

// Review-deploy-only comment action for the clicked feature, keyed on the item's stable primary-key
// column. Self-contained so the map doesn't have to hold a second piece of selection state.
function ReviewComment({ item, props }: { item: StacDoc; props: Record<string, unknown> }) {
  const [open, setOpen] = useState(false);
  const pk = primaryKeyOf(item);
  const pkVal = props[pk];
  if (!pk || pkVal == null) return null;
  const rowVal = String(pkVal);
  return (
    <div className="mt-2 border-t border-border pt-2">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="rounded border border-amber-500/50 bg-amber-500/10 px-2 py-1 text-xs font-medium text-amber-700 hover:bg-amber-500/20"
      >
        💬 Comment on this feature
      </button>
      {open && (
        <CommentsPanel itemId={String(item.id ?? "")} target={{ kind: "row", rowKey: pk, rowVal }}
          label={`Comments on ${pk} ${rowVal}`} />
      )}
    </div>
  );
}

export function FeatureCard({ item, props, onOpenRelated, onClear, onZoom }: {
  item: StacDoc; props: Record<string, unknown>;
  onOpenRelated?: (relatedKey: string, value: string) => void; onClear?: () => void;
  onZoom?: () => void;   // zoomed out, one lit line is too small to see, so offer to fly to it
}) {
  const joins = relatedJoins(item);
  const summary = summaryFieldsOf(item);
  const idFields = (summary.length ? summary : Object.keys(props)).filter((k) => props[k] != null && props[k] !== "").slice(0, 5);
  const title = String(props[summary[0]] || props[idFields[0]] || item.properties?.title || "Feature");
  return (
    <section className="mt-3 rounded-md border border-border bg-card p-3">
      <div className="mb-1.5 flex items-center justify-between">
        <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Selected feature</span>
        <button type="button" onClick={onClear} aria-label="Clear selection" className="text-muted-foreground hover:text-foreground">✕</button>
      </div>
      <div className="flex items-start justify-between gap-2">
        <div className="text-sm font-semibold">{title}</div>
        {onZoom && (
          <button type="button" onClick={onZoom}
            className="shrink-0 rounded border border-border px-2 py-0.5 text-xs text-foreground hover:border-primary pointer-coarse:min-h-11">
            Zoom to
          </button>
        )}
      </div>
      <div className="mt-1 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs">
        {idFields.map((k) => (
          <div key={k} className="contents"><div className="text-muted-foreground">{k}</div><div className="break-words min-w-0">{String(props[k])}</div></div>
        ))}
      </div>
      {joins.length > 0 && (
        <div className="mt-2 border-t border-border pt-2">
          <div className="mb-0.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">Related</div>
          {joins.map((j) => <PeekRow key={`${j.key}:${j.childField}`} join={j} value={props[j.parentField]} onOpen={onOpenRelated} />)}
        </div>
      )}
      {IS_REVIEW && <ReviewComment item={item} props={props} />}
    </section>
  );
}
