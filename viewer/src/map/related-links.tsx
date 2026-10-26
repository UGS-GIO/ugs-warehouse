// Related-table affordances under a clicked feature's props on the Map page. Names come straight
// from the index (no fetch on click); the join columns, COUNT and rows load only when one is opened,
// in the Info panel. Kept out of map.tsx so it renders without maplibre.
import { describeData, partitionRelated } from "@/stac";
import type { OpenRelated, RelatedTablesInfo } from "./map";
import { ReferenceTables } from "./reference-tables";

export function RelatedLinks({ info, props, onOpen }: {
  info?: RelatedTablesInfo; props: Record<string, unknown>; onOpen: (r: OpenRelated) => void;
}) {
  if (!info?.tables.length) return null;
  const button = (t: RelatedTablesInfo["tables"][number]) => {
    const text = describeData({ table: true, rows: t.rows });
    return (
      <span key={t.key} className="inline-flex items-center gap-1.5">
        <button type="button"
          onClick={() => onOpen({ itemHref: info.itemHref, relatedKey: t.key, title: t.title, props })}
          className="inline-flex items-center gap-1 rounded-md border border-border bg-card px-2.5 py-1 text-xs font-medium text-foreground transition-colors hover:border-primary/40 hover:bg-primary/5 hover:text-primary">
          {t.title} <span aria-hidden className="text-primary">→</span>
        </button>
        {text && <span className="text-2xs text-muted-foreground">{text}</span>}
      </span>
    );
  };
  const { own: primary, reference: refs } = partitionRelated(info.tables, (t) => Boolean(t.reference));
  return (
    <div className="mt-3 border-t border-border pt-2.5">
      {primary.length > 0 && (
        <>
          <div className="mb-1.5 text-3xs font-semibold uppercase tracking-wider text-muted-foreground">Related tables</div>
          <div className="flex flex-wrap gap-1.5">{primary.map(button)}</div>
        </>
      )}
      {/* Reference tables come after the feature's own, collapsed unless they are all there is. */}
      {refs.length > 0 && (
        <ReferenceTables titles={refs.map((t) => t.title)} defaultOpen={!primary.length}
          className={primary.length ? "mt-2" : ""}>
          {() => <div className="mt-1.5 flex flex-wrap gap-1.5">{refs.map(button)}</div>}
        </ReferenceTables>
      )}
    </div>
  );
}
