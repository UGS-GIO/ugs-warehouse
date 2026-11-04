// The map's detail pane for the selected item: what it is, the files to download, and a way to the
// full item page. On the review deploy it also carries the diff and comments.
import { Link } from "@tanstack/react-router";

import { AssetsPanel } from "@/catalog/endpoints-panel";
import { CatalogReview, MetaList } from "@/catalog/item-detail";
import { glanceRowsOf, itemFacts } from "@/catalog/item-view";
import { PropertyTable } from "@/catalog/property-table";
import { IS_REVIEW, type StacDoc } from "@/stac";

export function MapDetail({ item, loading, collectionId }: { item?: StacDoc; loading: boolean; collectionId: string }) {
  if (loading) return <em>Loading item…</em>;
  if (!item) return <em className="text-muted-foreground">Click a layer's ⓘ to see what it is and download its files.</em>;
  const p = item.properties ?? {};
  const description = typeof p.description === "string" && p.description;
  const glance = glanceRowsOf(item);
  return (
    // Two columns once the dock is wide, so the tables don't stretch across the whole map.
    <div className="@container">
      <div className="grid gap-x-6 gap-y-3 @3xl:grid-cols-2">
        <div className="min-w-0 space-y-3">
          <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-1">
            <div className="min-w-0">
              <h2 className="text-base font-semibold">{String(p.title ?? item.id ?? "")}</h2>
              <p className="text-sm text-muted-foreground">{itemFacts(item, collectionId)}</p>
            </div>
            <Link to="/catalog" search={{ c: collectionId, i: String(item.id ?? "") }}
              className="shrink-0 rounded border border-border bg-card px-2.5 py-1 text-sm font-medium text-foreground no-underline hover:border-primary">
              Open full page
            </Link>
          </div>
          {description && <p className="line-clamp-4 text-sm text-muted-foreground">{description}</p>}
          <AssetsPanel item={item} title="Download to your computer"
            note="The published files, for GIS software such as QGIS or ArcGIS Pro. For Shapefile, GeoPackage or CSV, use Export on the full page." />
          {IS_REVIEW && <CatalogReview item={item} />}
        </div>
        <div className="min-w-0 space-y-3">
          {glance.length > 0 && <MetaList rows={glance} />}
          <details>
            <summary className="cursor-pointer text-sm text-muted-foreground hover:text-foreground">All properties</summary>
            <PropertyTable properties={p} className="mt-2" />
          </details>
        </div>
      </div>
    </div>
  );
}
