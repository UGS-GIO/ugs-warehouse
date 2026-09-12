// Item detail: two layouts share one component. `page` = the full-width catalog/browse detail (a
// 2/3 · 1/3 grid); `drawer` = the single-column stack that fits the 560px Discover result drawer. Both
// reuse the same capability panels (Preview, Downloads, Endpoints, Related, Review, schema, STAC JSON).
import { type ReactNode, useEffect, useRef, useState } from "react";

import type { ItemRef } from "./browse";
import { AddToMapButton } from "@/map/add-to-map-button";
import { CommentsPanel } from "@/review/comments-panel";
import { DataExplorer } from "@/data/data-explorer";
import { PhotoGallery } from "./photo-gallery";

import { DiffPanel } from "@/review/diff-panel";
import { FeatureCard } from "@/map/feature-card";
import { Preview } from "./asset-viewer";
import { EndpointsPanel } from "./endpoints-panel";
import { DownloadsPanel } from "./downloads-panel";
import { bylineParts, categorize, curatedDerived, kindLabel, type MetaRow, recordCountLabel } from "./item-view";
import { T } from "@/shell/page";
import { PropertyTable } from "./property-table";
import { SchemaTable } from "./schema-table";
import { StacJson } from "./stac-json";
import { LayerStatusControl, statusClass, statusLabel, useItemStatuses } from "@/review/review-status";
import { type Asset, citeLink, contentsOf, IS_REVIEW, ownForeignKeys, relatedAssets,
  relatedJoins, relatedLinks, type StacDoc, tableColumns, viaLink } from "@/stac";
import { usePreviewMap } from "@/map/preview-map";
import { C, humanize } from "@/ui/ui";

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
  const { featureRelated, clearRelated } = usePreviewMap();
  const sectionRef = useRef<HTMLDivElement>(null);
  // A map-feature click (wired via the preview-map context) auto-opens its related table and
  // scrolls this section into view, so the click's result is visible without manual scrolling.
  useEffect(() => {
    if (!featureRelated) return;
    setOpenTables((prev) => new Set(prev).add(featureRelated.relatedKey));
    const t = setTimeout(() => sectionRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 0);
    return () => clearTimeout(t);
  }, [featureRelated]);
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
    <section ref={sectionRef} className="mt-4 rounded-md border border-border p-3">
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
              <li key={i}>
                <code>{fk.fields.join(", ")}</code> →{" "}
                {fk.reference.href
                  ? <a href={fk.reference.href} className="font-medium text-primary hover:underline" download>{humanize(fk.reference.resource)}</a>
                  : <span className="font-medium">{humanize(fk.reference.resource)}</span>}
                .<code>{fk.reference.fields.join(", ")}</code>
              </li>
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
                {openTables.has(key) && (() => {
                  const childField = relatedJoins(item).find((j) => j.key === key)?.childField;
                  const preset = featureRelated?.relatedKey === key && childField
                    ? { col: childField, kind: "exact" as const, value: featureRelated.value }
                    : undefined;
                  return <DataExplorer key={asset.href} href={asset.href} presetFilter={preset} onClearPreset={clearRelated} />;
                })()}
                {openGalleries.has(key) && <PhotoGallery href={asset.href} />}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

// The clicked map feature's card, rendered inline below Preview — reads the selection out of the
// preview-map context so both layouts share the same behavior without lifting state up further.
function SelectedFeatureCard({ item }: { item: StacDoc }) {
  const { selectedFeature, clearSelection, openRelated } = usePreviewMap();
  const ref = useRef<HTMLDivElement>(null);
  // The card only mounts below the map once a feature is clicked; on a tall preview it lands below
  // the fold, so pull it into view on each new selection (mirrors RelatedPanel). `nearest` keeps as
  // much of the map — and the highlighted feature — in view as possible.
  useEffect(() => {
    if (!selectedFeature) return;
    const t = setTimeout(() => ref.current?.scrollIntoView({ behavior: "smooth", block: "nearest" }), 0);
    return () => clearTimeout(t);
  }, [selectedFeature]);
  if (!selectedFeature) return null;
  return <div ref={ref}><FeatureCard item={item} props={selectedFeature.props} onOpenRelated={openRelated} onClear={clearSelection} /></div>;
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

// ── page-layout pieces (the drawer keeps its plain single-column stack below) ────────────────────
function Badge({ children, tone = "default" }: { children: ReactNode; tone?: "default" | "primary" | "warn" }) {
  const cls = tone === "primary" ? "border-primary/30 bg-primary/10 text-primary"
    : tone === "warn" ? "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-400"
      : "border-border bg-muted text-muted-foreground";
  return <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium ${cls}`}>{children}</span>;
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section aria-label={title} className="space-y-3">
      <h2 className="text-base font-semibold tracking-tight text-foreground">{title}</h2>
      {children}
    </section>
  );
}

// One labeled metadata group (Curated / Derived) as a key→value list; hidden when it has no rows.
function MetaGroup({ label, rows }: { label: string; rows: MetaRow[] }) {
  if (!rows.length) return null;
  return (
    <div className="space-y-2">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{label}</h3>
      <dl className="divide-y divide-border overflow-hidden rounded-lg border border-border">
        {rows.map((r) => (
          <div key={r.label} className="flex items-baseline justify-between gap-4 bg-card px-3 py-2">
            <dt className="shrink-0 text-sm text-muted-foreground">{r.label}</dt>
            <dd className="min-w-0 break-words text-right text-sm font-medium text-foreground">{r.value}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

// Header review badge — reads the shared item-status cache (the same query LayerStatusControl uses, so
// no extra fetch); review deploy only.
function ReviewBadge({ itemId }: { itemId: string }) {
  const { data } = useItemStatuses();
  const list = (data ?? []) as { item_id: string; status: string }[];
  const status = list.find((s) => s.item_id === itemId)?.status ?? "pending";
  return <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium ${statusClass(status)}`}>{statusLabel(status)}</span>;
}

export function ItemDetail({ collectionId, item, onBack, onMap, onExplore, layout = "drawer" }: {
  collectionId: string; item?: StacDoc; onBack: () => void; onMap: () => void;
  onExplore?: () => void;          // full-screen Preview (offered in the Discover drawer)
  layout?: "page" | "drawer";      // page = full-width 2/3·1/3 grid; drawer = single column
}) {
  if (!item) return <em className={C.muted}>Loading…</em>;
  const p = item.properties ?? {};
  const hasGeom = Boolean(item.geometry || item.bbox);
  const via = viaLink(item);
  const cite = citeLink(item);

  // The way-out buttons, shared by both layouts.
  const actions = (
    <div className="flex flex-wrap gap-2">
      {hasGeom && (
        <button onClick={onMap} className="inline-block rounded bg-emerald-700 px-2.5 py-1 text-xs text-white hover:bg-emerald-800">
          View on map ›
        </button>
      )}
      {/* "View on map" isolates this item; "+ Add to map" accumulates it into the active set without
          leaving. No layerId: this panel always shows the OPEN item, so the button targets it.
          Self-gates: renders only when that item is a real map layer. */}
      <AddToMapButton />
      {onExplore && (
        <button onClick={onExplore} className="inline-block rounded border border-border px-2.5 py-1 text-xs text-foreground hover:border-primary">
          Explore ⤢
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
  );

  const crumb = (
    <div className="mb-1.5 text-xs">
      <span className={C.crumb} onClick={onBack}>{collectionId}</span>
      <span className={C.muted}> / {item.id}</span>
    </div>
  );

  if (layout === "page") {
    // The item-view getters read an ItemRef; build a lightweight one over the resolved doc.
    const it: ItemRef = { collId: collectionId, href: "", data: item };
    const cat = categorize(it);
    const rows = recordCountLabel(it);
    const byline = bylineParts(it);
    const meta = curatedDerived(p);
    return (
      // No outer padding — the caller (Browse) already wraps this in C.wrap.
      <>
        {crumb}
        <header>
          <div className="flex flex-wrap items-center gap-1.5">
            <Badge tone="primary">{kindLabel(it)}</Badge>
            <Badge>{cat.label}</Badge>
            {hasGeom && <Badge>Mappable</Badge>}
            {rows && <Badge>{rows}</Badge>}
            {IS_REVIEW && item.id && <ReviewBadge itemId={String(item.id)} />}
          </div>
          <h1 className="mt-2 font-display text-2xl font-semibold tracking-tight text-foreground sm:text-3xl">{String(p.title ?? item.id ?? "")}</h1>
          <div className="mt-0.5 font-mono text-xs text-muted-foreground">{item.id}</div>
          {byline.length > 0 && <p className="mt-1.5 text-sm text-muted-foreground">{byline.join(" · ")}</p>}
          {typeof p.description === "string" && <p className="mt-3 max-w-3xl text-muted-foreground">{p.description}</p>}
        </header>
        {/* min-w-0 on the grid CHILDREN, not a width on anything: a grid item defaults to
            min-width:auto, so the single mobile column sized itself to its widest descendant's
            max-content (861px inside a 360px phone) and everything below inherited that. */}
        <div className="mt-5 grid gap-8 lg:grid-cols-3">
          <div className="min-w-0 space-y-6 lg:col-span-2">
            {actions}
            <Preview item={item} />
            <SelectedFeatureCard item={item} />
            <IssueContents item={item} />
            <Section title="Data schema"><SchemaTable columns={tableColumns(item)} /></Section>
            <RelatedPanel item={item} />
            {IS_REVIEW && <CatalogReview item={item} />}
          </div>
          <aside className="min-w-0 space-y-6">
            <Section title="Metadata">
              {meta.curated.length || meta.derived.length ? (
                <div className="space-y-5">
                  <MetaGroup label="Curated at upload" rows={meta.curated} />
                  <MetaGroup label="Derived" rows={meta.derived} />
                </div>
              ) : <p className="text-sm text-muted-foreground">No metadata published.</p>}
            </Section>
            <DownloadsPanel item={item} />
            <EndpointsPanel item={item} />
            <Section title="Developer"><StacJson item={item} title={`${item.id} — STAC JSON`} /></Section>
            <details className="rounded-lg border border-border">
              <summary className="cursor-pointer px-3 py-2 text-sm font-medium text-muted-foreground">All properties</summary>
              <div className="px-3 pb-2"><PropertyTable properties={p} className="mt-0" /></div>
            </details>
          </aside>
        </div>
      </>
    );
  }

  // drawer — the single-column stack (fits the 560px Discover drawer).
  return (
    <>
      <div className="mb-4 border-b border-border pb-3">
        {crumb}
        {/* Title leads. The machine id is the subtitle — it was set in blue mono ABOVE the human
            name, so the thing nobody reads outranked the thing everybody does. */}
        <h1 className={T.pageTitle}>{String(p.title ?? item.id ?? "")}</h1>
        <div className="mt-0.5 font-mono text-xs text-muted-foreground">{item.id}</div>
      </div>
      <Preview item={item} />
      <SelectedFeatureCard item={item} />
      {/* Below the map/table, not above it: the description is context for what you are looking at,
          and putting prose between the title and the data pushed the data down the page. */}
      {typeof p.description === "string" && (
        <p className="mt-3 max-w-full text-muted-foreground">{p.description}</p>
      )}
      <div className="mt-1.5">{actions}</div>
      <IssueContents item={item} />
      <DownloadsPanel item={item} />
      <EndpointsPanel item={item} />
      <RelatedPanel item={item} />
      {IS_REVIEW && <CatalogReview item={item} />}
      <PropertyTable properties={p} />
    </>
  );
}

