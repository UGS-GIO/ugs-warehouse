// The Preview view (/preview?c=&i=): a locked full-viewport shell that hosts the EXISTING item
// Preview at full height. The persistent preview map already lives in App's PreviewMapProvider, so the
// map portals in as usual — this is just a slim bar (title · Back · View on map) over the same Preview
// the item-detail page and the Discover drawer render. Reached from the drawer's "Explore" action.
import { Preview } from "../catalog/asset-viewer";
import type { StacDoc } from "../stac";

export function PreviewView({ item, loading, onBack, onMap }: {
  item?: StacDoc; loading: boolean; onBack: () => void; onMap: () => void;
}) {
  const p = item?.properties ?? {};
  const title = String(p.title ?? item?.id ?? "");
  const hasGeom = Boolean(item?.geometry || item?.bbox);
  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden bg-background text-foreground">
      <div className="flex shrink-0 items-center gap-3 border-b border-border bg-background px-3 py-2">
        <button type="button" onClick={onBack}
          className="shrink-0 rounded px-2 py-1 text-sm text-muted-foreground hover:bg-muted">← Back</button>
        <div className="min-w-0 flex-1">
          <div className="truncate text-base font-semibold text-foreground" title={title}>
            {title || (loading ? "Loading…" : "Preview")}
          </div>
          {item?.id && <div className="truncate font-mono text-xs text-muted-foreground">{String(item.id)}</div>}
        </div>
        {hasGeom && (
          <button type="button" onClick={onMap}
            className="shrink-0 rounded bg-emerald-700 px-2.5 py-1 text-xs text-white hover:bg-emerald-800">
            View on map ›
          </button>
        )}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-3 py-3 sm:px-4">
        {loading ? (
          <p className="p-16 text-center text-sm text-muted-foreground">Loading the item…</p>
        ) : item ? (
          <Preview item={item} />
        ) : (
          <p className="p-16 text-center text-sm text-muted-foreground">No item selected to preview.</p>
        )}
      </div>
    </div>
  );
}
