// The developer reference at the end of the Guide: the URL patterns, the STAC extensions in play,
// and a live tree of the catalog's top-level collections, from the app's own CATALOG_URL and loaded
// rootChildren (no new fetch). The Guide's tool sections already carry the code samples.
import { type ReactNode, useMemo } from "react";

import type { CollectionSummary } from "@/catalog/browse";
import { humanize } from "@/ui/ui";

// CDN origin behind the catalog (…/warehouse/stac/catalog.json → https://maps-assets.geology.utah.gov).
const cdnBase = (catalogUrl: string): string => {
  try { return new URL(catalogUrl).origin; } catch { return ""; }
};

function Panel({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section aria-label={title} className="space-y-4">
      <h3 className="text-lg font-semibold tracking-tight text-foreground">{title}</h3>
      {children}
    </section>
  );
}

function CatalogTree({ nodes }: { nodes: CollectionSummary[] }) {
  return (
    <div className="space-y-3">
      {nodes.map((node) => (
        <div key={node.href} role="group" aria-label={node.title ?? node.id}
          className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-baseline justify-between gap-3">
            <h4 className="font-semibold text-card-foreground">{node.title ?? humanize(node.id)}</h4>
            <span className="shrink-0 font-mono text-sm font-semibold text-primary">{node.count?.toLocaleString() ?? ""}</span>
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1">
            <code className="font-mono text-xs text-muted-foreground">{node.id}</code>
            <a href={node.href} target="_blank" rel="noopener" className="font-mono text-xs text-primary hover:underline" title="View STAC JSON">
              STAC ↗
            </a>
          </div>
        </div>
      ))}
    </div>
  );
}

export function Developers({ catalogUrl, groups }: { catalogUrl: string; groups: CollectionSummary[] }) {
  const cdn = cdnBase(catalogUrl);
  const nodes = useMemo(() => [...groups].sort((a, b) => (b.count ?? 0) - (a.count ?? 0)), [groups]);

  const endpoints: { label: string; value: string }[] = [
    { label: "STAC catalog", value: catalogUrl },
    { label: "All items", value: catalogUrl.replace(/catalog\.json$/, "items.json") },
    { label: "Collection items (stac-geoparquet)", value: `${cdn}/warehouse/stac/<collection>/items.parquet` },
    { label: "GeoParquet", value: `${cdn}/warehouse/geoparquet/<topic>/<topic>.parquet` },
    { label: "PMTiles", value: `${cdn}/warehouse/pmtiles/<topic>/<topic>.pmtiles` },
    { label: "COG (rasters)", value: `${cdn}/cog/<collection>/<id>.cog.tif` },
    { label: "COG (publication maps)", value: `${cdn}/geolmap/cogs/<series_id>.cog.tif` },
    { label: "OGC API Features", value: `each item's link with rel="service"` },
  ];

  const extensions: { name: string; field: string; url?: string }[] = [
    { name: "Projection", field: "proj:code", url: "https://stac-extensions.github.io/projection/v2.0.0/schema.json" },
    { name: "Table", field: "table:columns", url: "https://stac-extensions.github.io/table/v1.2.0/schema.json" },
    { name: "Web Map Links", field: "pmtiles", url: "https://stac-extensions.github.io/web-map-links/v1.3.0/schema.json" },
    { name: "Classification", field: "classification:classes", url: "https://stac-extensions.github.io/classification/v2.0.0/schema.json" },
    { name: "File", field: "file:size, file:checksum", url: "https://stac-extensions.github.io/file/v2.1.0/schema.json" },
    { name: "Version", field: "version, deprecated", url: "https://stac-extensions.github.io/version/v1.2.0/schema.json" },
    { name: "UGS fields", field: "ugs:* (dbt_schema, row_count, foreign_keys, series, renders and others)" },
  ];

  return (
    <section id="for-developers" className="w-full max-w-[62rem] scroll-mt-24 space-y-10 py-8">
      <header className="space-y-2">
        <h2 className="text-2xl font-semibold tracking-tight text-foreground">For developers</h2>
        <p className="max-w-2xl text-muted-foreground">
          The catalog is static <strong className="font-semibold text-foreground">STAC</strong> on the CDN. You need no key, and
          a browser can read it directly. The data is GeoParquet, PMTiles and COG. Vector layers also have an OGC API
          Features service.
        </p>
      </header>

      <Panel title="Endpoints">
        <div className="overflow-x-auto rounded-lg border border-border">
          <table className="w-full border-collapse text-sm">
            <tbody>
              {endpoints.map((e) => (
                <tr key={e.label} className="border-b border-border last:border-0">
                  <td className="whitespace-nowrap px-3 py-2 font-medium text-foreground">{e.label}</td>
                  <td className="px-3 py-2"><code className="break-all font-mono text-xs text-muted-foreground">{e.value}</code></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>


      <Panel title="STAC extensions">
        <ul className="divide-y divide-border overflow-hidden rounded-lg border border-border">
          {extensions.map((ext) => (
            <li key={ext.name} className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 bg-card px-3 py-2">
              <span className="flex items-baseline gap-2">
                <span className="font-medium text-card-foreground">{ext.name}</span>
                <code className="font-mono text-xs text-muted-foreground">{ext.field}</code>
              </span>
              {ext.url && <a href={ext.url} target="_blank" rel="noopener" className="text-xs text-primary hover:underline">schema</a>}
            </li>
          ))}
        </ul>
      </Panel>

      <Panel title="Catalog">
        {nodes.length ? <CatalogTree nodes={nodes} /> : <p className="text-sm text-muted-foreground">Loading the catalog…</p>}
      </Panel>
    </section>
  );
}
