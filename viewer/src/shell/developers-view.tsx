// The Developers view (NavMenu overflow): the whole warehouse is a static, CORS-open STAC catalog on
// the CDN, so this page is the "read it yourself" reference — the endpoint URLs, copy-paste snippets
// (Python / DuckDB / OGC / MapLibre), the STAC extensions in play, and a live tree of the catalog's
// top-level collections. Parameterized by the app's own CATALOG_URL / CDN base / loaded rootChildren
// (no new fetch). Ported from ugs-data-catalog/src/routes/developers.tsx onto UDS tokens. Distinct from
// Architecture (a conceptual platform diagram) and Guide (end-user prose) — it duplicates neither.
import { type ReactNode, useMemo } from "react";

import type { CollectionSummary } from "../catalog/browse";
import { humanize } from "../ui/ui";

// CDN origin behind the catalog (…/warehouse/stac/catalog.json → https://maps-assets.geology.utah.gov).
const cdnBase = (catalogUrl: string): string => {
  try { return new URL(catalogUrl).origin; } catch { return ""; }
};

function Snippet({ label, code }: { label: string; code: string }) {
  return (
    <div className="space-y-1.5">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{label}</h3>
      <pre className="overflow-x-auto rounded-lg border border-border bg-muted/40 p-3 font-mono text-xs leading-relaxed text-foreground">{code}</pre>
    </div>
  );
}

function Panel({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section aria-label={title} className="space-y-4">
      <h2 className="text-lg font-semibold tracking-tight text-foreground">{title}</h2>
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
            <h3 className="font-semibold text-card-foreground">{node.title ?? humanize(node.id)}</h3>
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

  const snippets: { label: string; code: string }[] = [
    { label: "Python", code:
`import httpx

# The catalog is static + CORS-open — read it straight from the CDN.
root = httpx.get("${catalogUrl}").json()
for link in root["links"]:
    if link["rel"] == "child":
        print(link.get("title"), link["href"])` },
    { label: "DuckDB", code:
`INSTALL spatial; LOAD spatial;

-- Every vector topic publishes a GeoParquet archive on the CDN.
SELECT *
FROM read_parquet('${cdn}/warehouse/geoparquet/<topic>/<topic>.parquet')
LIMIT 10;` },
    { label: "OGC API Features", code:
`# Each vector topic's STAC item carries a rel="service" OGC API Features URL.
item = httpx.get(
  "${cdn}/warehouse/stac/ugs-serving-topics/<sub>/<topic>/<topic>.json"
).json()
service = next(l["href"] for l in item["links"] if l["rel"] == "service")
features = httpx.get(f"{service}/items?limit=10&f=json").json()` },
    { label: "MapLibre (PMTiles)", code:
`// PMTiles vector tiles stream from the CDN via the pmtiles:// protocol.
map.addSource("topic", {
  type: "vector",
  url: "pmtiles://${cdn}/warehouse/pmtiles/<topic>/<topic>.pmtiles",
})` },
  ];

  const endpoints: { label: string; value: string }[] = [
    { label: "STAC catalog", value: catalogUrl },
    { label: "GeoParquet", value: `${cdn}/warehouse/geoparquet/<topic>/<topic>.parquet` },
    { label: "PMTiles", value: `${cdn}/warehouse/pmtiles/<topic>/<topic>.pmtiles` },
    { label: "COG (rasters)", value: `${cdn}/warehouse/cog/<id>/<id>.tif` },
    { label: "OGC API Features", value: `each item's link with rel="service"` },
  ];

  const extensions: { name: string; field: string; url?: string }[] = [
    { name: "Projection", field: "proj:code", url: "https://stac-extensions.github.io/projection/v2.0.0/schema.json" },
    { name: "Table", field: "table:columns", url: "https://stac-extensions.github.io/table/v1.2.0/schema.json" },
    { name: "Web Map Links", field: "pmtiles", url: "https://stac-extensions.github.io/web-map-links/v1.3.0/schema.json" },
    { name: "Classification", field: "classification:classes", url: "https://stac-extensions.github.io/classification/v2.0.0/schema.json" },
    { name: "UGS custom", field: "ugs:* (dbt_schema, row_count, foreign_keys, series, renders, …)" },
  ];

  return (
    <div className="mx-auto w-full max-w-5xl space-y-10 px-4 py-8 sm:px-6">
      <header className="space-y-2">
        <h1 className="font-display text-3xl tracking-tight text-foreground">Developers</h1>
        <p className="max-w-2xl text-muted-foreground">
          The whole UGS warehouse is a static, CORS-open <strong className="font-semibold text-foreground">STAC</strong> catalog
          on the CDN — GeoParquet, PMTiles, COG, and an OGC API Features service, all reachable without a key or a backend.
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

      <Panel title="Read the catalog">
        <div className="grid grid-cols-1 gap-5 md:grid-cols-2">
          {snippets.map((s) => <Snippet key={s.label} label={s.label} code={s.code} />)}
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
    </div>
  );
}
