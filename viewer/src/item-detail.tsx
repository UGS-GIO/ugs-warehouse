// Item detail page: header, preview, related layers, review state, properties.
import { useState } from "react";

import { CommentsPanel } from "./comments-panel";
import { DataExplorer } from "./data-explorer";
import { PhotoGallery } from "./photo-gallery";

import { DiffPanel } from "./diff-panel";
import { Preview } from "./asset-viewer";
import { EndpointsPanel } from "./endpoints-panel";
import { DownloadsPanel } from "./downloads-panel";
import { T } from "./page";
import { PropertyTable } from "./property-table";
import { LayerStatusControl } from "./review-status";
import { type Asset, citeLink, contentsOf, IS_REVIEW, ownForeignKeys, relatedAssets,
  relatedLinks, type StacDoc, tableColumns, viaLink } from "./stac";
import { C, humanize } from "./ui";

const relatedViewerHref = (stacHref: string): string => {
  const m = stacHref.match(/\/([^/]+)\/([^/]+)\/[^/]+\.json(?:\?.*)?$/);
  return m ? `?c=${encodeURIComponent(m[1])}&i=${encodeURIComponent(m[2])}` : stacHref;
};

function RelatedPanel({ item }: { item: StacDoc }) {
  const links = relatedLinks(item);
  const tables = relatedAssets(item);
  const fks = ownForeignKeys(item);
  // Any number of related tables can be expanded inline at once (not one-or-the-other). Photo tables
  // additionally offer a thumbnail Gallery. Both use a Set so multiple stay open.
  const [openTables, setOpenTables] = useState<Set<string>>(new Set());
  const [openGalleries, setOpenGalleries] = useState<Set<string>>(new Set());
  const toggleIn = (set: React.Dispatch<React.SetStateAction<Set<string>>>) => (key: string) =>
    set((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  const toggleTable = toggleIn(setOpenTables);
  const toggleGallery = toggleIn(setOpenGalleries);
  const isPhotos = (key: string, asset: Asset) => /photo/i.test(key) || /photo/i.test(asset.title ?? "");
  if (!links.length && !tables.length && !fks.length) return null;
  return (
    <section className="mt-4 rounded-md border border-border p-3">
      <h3 className="text-sm font-semibold">Related</h3>
      {links.length > 0 && (
        <div className="mt-1.5">
          <div className="text-xs uppercase tracking-wide text-muted-foreground">Related layers</div>
          <ul className="mt-1 space-y-0.5">
            {links.map((l, i) => (
              <li key={i}><a href={relatedViewerHref(l.href)} className="text-primary hover:underline">{l.title ?? "related"} ›</a></li>
            ))}
          </ul>
        </div>
      )}
      {fks.length > 0 && (
        <div className="mt-2">
          <div className="text-xs uppercase tracking-wide text-muted-foreground">This layer references</div>
          <ul className="mt-1 space-y-0.5 text-xs">
            {fks.map((fk, i) => (
              <li key={i}><code>{fk.fields.join(", ")}</code> → <span className="font-medium">{humanize(fk.reference.resource)}</span>.<code>{fk.reference.fields.join(", ")}</code></li>
            ))}
          </ul>
        </div>
      )}
      {tables.length > 0 && (
        <div className="mt-2">
          <div className="text-xs uppercase tracking-wide text-muted-foreground">Related tables</div>
          <ul className="mt-1 space-y-1 text-xs">
            {tables.map(({ key, asset }) => (
              <li key={key}>
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">{asset.title ?? key}</span>
                  <button className="text-primary hover:underline"
                    onClick={() => toggleTable(key)}>
                    {openTables.has(key) ? "Hide" : "View"}
                  </button>
                  {isPhotos(key, asset) && (
                    <button className="text-primary hover:underline" onClick={() => toggleGallery(key)}>
                      {openGalleries.has(key) ? "Hide gallery" : "Gallery"}
                    </button>
                  )}
                  <a href={asset.href} className="text-primary hover:underline" download>Parquet ↓</a>
                  {asset["ugs:foreign_keys"]?.map((fk, i) => (
                    <span key={i} className="text-muted-foreground">(<code>{fk.fields.join(", ")}</code> → this)</span>
                  ))}
                </div>
                {/* View the related parquet in the same DuckDB-wasm explorer — paged/virtualized,
                    range-read (never downloads the whole file). No geometry → a plain data table. */}
                {openTables.has(key) && <DataExplorer key={asset.href} href={asset.href} />}
                {openGalleries.has(key) && <PhotoGallery href={asset.href} />}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

// "In this issue" — the Survey Notes table of contents (warehouse parses it from the PDF). Each
// article deep-links the PDF to its page (#page=N), where a page number was captured.
function IssueContents({ item }: { item: StacDoc }) {
  const toc = contentsOf(item);
  if (!toc) return null;
  const pdf = Object.values(item.assets ?? {}).find((a) => a.type === "application/pdf")?.href;
  return (
    <section className="mt-4 rounded-md border border-border bg-card p-3">
      <h3 className="text-sm font-semibold">In this issue</h3>
      <ol className="mt-1.5 divide-y divide-border text-sm">
        {toc.map((e, i) => {
          const href = pdf ? (e.page != null ? `${pdf}#page=${e.page}` : pdf) : undefined;
          const label = <><span className="text-foreground">{e.title}</span>
            {e.page != null && <span className="ml-2 text-xs text-muted-foreground">p. {e.page}</span>}</>;
          return (
            <li key={i} className="py-1">
              {href
                ? <a href={href} target="_blank" rel="noopener" className="no-underline hover:underline">{label}</a>
                : label}
            </li>
          );
        })}
      </ol>
    </section>
  );
}

// Review deploy only: reviews baked into the catalog detail — a diff vs the live version, an
// item-level comment thread, and a per-column comment button (flag a wrong name/unit/type).
function CatalogReview({ item }: { item: StacDoc }) {
  const id = String(item.id ?? "");
  const geoparquet = Object.entries(item.assets ?? {})
    .find(([k, a]) => /parquet/i.test(String(a.type ?? "")) || /parquet|geoparquet/i.test(k))?.[1]?.href;
  const cols = tableColumns(item);
  const [openCol, setOpenCol] = useState<string | null>(null);
  if (!id) return null;
  return (
    <section className="mt-4 rounded-md border border-amber-500/40 bg-amber-500/[0.04] p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold">Review</h3>
        <LayerStatusControl itemId={id} />
      </div>
      {geoparquet && <DiffPanel stem={id} reviewParquetUrl={geoparquet} />}
      <CommentsPanel itemId={id} />
      {cols && cols.length > 0 && (
        <div className="mt-3">
          <div className="mb-1 text-xs uppercase tracking-wide text-muted-foreground">Columns</div>
          <ul className="divide-y divide-border rounded border border-border text-xs">
            {cols.map((c) => (
              <li key={c.name} className="px-2 py-1">
                <div className="flex items-center gap-2">
                  <code className="font-medium text-foreground">{c.name}</code>
                  {c.type && <span className="text-muted-foreground">{c.type}</span>}
                  {c.description && <span className="truncate text-muted-foreground">— {c.description}</span>}
                  <button
                    className="ml-auto shrink-0 text-primary hover:underline"
                    onClick={() => setOpenCol(openCol === c.name ? null : c.name)}>
                    {openCol === c.name ? "close" : "comment"}
                  </button>
                </div>
                {openCol === c.name && (
                  <CommentsPanel itemId={id} target={{ kind: "column", column: c.name }}
                    label={`Comments on “${c.name}”`} />
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

export function ItemDetail({ collectionId, item, onBack, onMap }: {
  collectionId: string; item?: StacDoc; onBack: () => void; onMap: () => void;
}) {
  if (!item) return <em className={C.muted}>Loading…</em>;
  const p = item.properties ?? {};
  const hasGeom = Boolean(item.geometry || item.bbox);
  const via = viaLink(item);
  const cite = citeLink(item);
  return (
    <>
      <div className="mb-4 border-b border-border pb-3">
        <div className="mb-1.5 text-xs">
          <span className={C.crumb} onClick={onBack}>{collectionId}</span>
          <span className={C.muted}> / {item.id}</span>
        </div>
        {/* Title leads. The machine id is the subtitle — it was set in blue mono ABOVE the human
            name, so the thing nobody reads outranked the thing everybody does. */}
        <h1 className={T.pageTitle}>{String(p.title ?? item.id ?? "")}</h1>
        <div className="mt-0.5 font-mono text-xs text-muted-foreground">{item.id}</div>
      </div>
      <Preview item={item} />
      {/* Below the map/table, not above it: the description is context for what you are looking at,
          and putting prose between the title and the data pushed the data down the page. */}
      {typeof p.description === "string" && (
        <p className="mt-3 max-w-[75ch] text-muted-foreground">{p.description}</p>
      )}
      <div className="mt-1.5 flex flex-wrap gap-2">
        {hasGeom && (
          <button onClick={onMap}
            className="inline-block rounded bg-emerald-700 px-2.5 py-1 text-xs text-white hover:bg-emerald-800">
            View on map ›
          </button>
        )}
        {via && (
          <a href={via.href} target="_blank" rel="noopener"
            className="inline-block rounded bg-primary px-2.5 py-1 text-xs text-primary-foreground no-underline hover:opacity-90">
            {via.title ?? "Publication page"} ↗
          </a>
        )}
        {cite && (
          <a href={cite.href} target="_blank" rel="noopener"
            className="inline-block rounded border border-border px-2.5 py-1 text-xs text-foreground no-underline hover:border-primary">
            Cite (DOI) ↗
          </a>
        )}
      </div>
      <IssueContents item={item} />
      <DownloadsPanel item={item} />
      <EndpointsPanel item={item} />
      <RelatedPanel item={item} />
      {IS_REVIEW && <CatalogReview item={item} />}
      <PropertyTable properties={p} />
    </>
  );
}

