// The map's right-hand detail pane: assets, properties, and — on the review deploy — the diff and
// comments for the selected item. Lived in app.tsx purely because the map route imported it there.
import { CommentsPanel } from "@/review/comments-panel";
import { DiffPanel } from "@/review/diff-panel";
import { PropertyTable } from "@/catalog/property-table";
import { IS_REVIEW, parquetAsset, shownAssets, type StacDoc } from "@/stac";

const asset = "mr-1.5 mt-0.5 inline-block rounded bg-primary px-2 py-1 text-xs text-primary-foreground no-underline hover:opacity-90";

export function MapDetail({ item, loading }: { item?: StacDoc; loading: boolean }) {
  if (loading) return <em>Loading item…</em>;
  if (!item) return <em className="text-muted-foreground">Pick an item to see detail, footprint, and assets.</em>;
  const p = item.properties ?? {};
  // Review deploy only: offer a diff of this _review item against its live _current counterpart.
  const isReview = IS_REVIEW;
  const geoparquet = parquetAsset(item)?.href;
  return (
    <>
      <h2 className="mb-1.5 text-base font-semibold">{String(p.title ?? item.id ?? "")}</h2>
      <div>
        {shownAssets(item.assets).map(([k, a]) => (
          <a key={k} className={asset} href={a.href} target="_blank" rel="noopener">{a.title ?? k}</a>
        ))}
      </div>
      {isReview && geoparquet && (
        <DiffPanel stem={String(item.id ?? "")} reviewParquetUrl={geoparquet} />
      )}
      {isReview && item.id && <CommentsPanel itemId={String(item.id)} />}
      <PropertyTable properties={p} className="mt-2" />
    </>
  );
}
