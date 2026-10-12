// Item detail: two layouts share one component. `page` = the full-width catalog/browse detail (a
// 2/3 · 1/3 grid); `drawer` = the single-column stack that fits the 560px Discover result drawer. Both
// reuse the same capability panels (Preview, Downloads, Endpoints, Related, Review, schema, STAC JSON).
import { type ReactNode, useRef, useState } from "react";
import { Link } from "@tanstack/react-router";

import type { ItemRef } from "./browse";
import { useScrollOnNew } from "@/lib/use-scroll-on-new";
import { AddToMapButton } from "@/map/add-to-map-button";
import { CommentsPanel } from "@/review/comments-panel";
import { DataExplorer } from "@/data/data-explorer";
import { PhotoGallery } from "./photo-gallery";

import { DiffPanel } from "@/review/diff-panel";
import { FieldsPanel, Preview } from "./asset-viewer";
import { AssetsPanel, EndpointsPanel, listedAssets } from "./endpoints-panel";
import { CubeStepsPanel, CubeStepsProvider } from "@/zarr/cube-steps-context";
import { DownloadsPanel } from "./downloads-panel";
import { aboutRows, bylineParts, categorize, collectionLabel, curatedDerived, kindLabel, type MetaRow, recordCountLabel } from "./item-view";
import { StacJson } from "./stac-json";
import { LayerStatusControl, statusClass, statusLabel, useItemStatuses } from "@/review/review-status";
import { type Asset, catalogItemHref, citeLink, collectionParts, contentsOf, hasFootprint, IS_REVIEW, ownForeignKeys, relatedAssets,
  pmtilesLink, relatedJoins, relatedLinks, type StacDoc, tableColumns, viaLink } from "@/stac";
import { usePreviewMap } from "@/map/preview-map";
import { C, humanize } from "@/ui/ui";
import { Unavailable } from "@/offline/offline-notice";
import { SaveLayerOffline } from "@/offline/save-layer-offline";

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
  // A map-feature click (via the preview-map context) auto-opens its related table — DERIVED, not an
  // effect that mutates `openTables`: deriving keeps the open set and the context from disagreeing,
  // and stops a remount (page↔drawer layout switch) from reopening a table on its own.
  const isOpen = (key: string) => openTables.has(key) || featureRelated?.relatedKey === key;
  // `block: "start"` here, not "nearest": the section expands as this renders, so put its heading at
  // the top rather than scrolling the minimum distance to a box that is still growing.
  useScrollOnNew(featureRelated && `${featureRelated.relatedKey}:${featureRelated.value}`,
                 sectionRef, { behavior: "smooth", block: "start" });
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
              <li key={i}><a href={catalogItemHref(l.href)} className="text-primary hover:underline">{l.title ?? "related"} ›</a></li>
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
                    {isOpen(key) ? "Hide" : "View"}
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
                {isOpen(key) && (() => {
                  const childField = relatedJoins(item).find((j) => j.key === key)?.childField;
                  const preset = featureRelated?.relatedKey === key && childField
                    ? { col: childField, kind: "exact" as const, value: featureRelated.value }
                    : undefined;
                  // Clearing the preset widens the table in place, so record it open BEFORE clearing
                  // the context — a table opened only by the click would otherwise close with it.
                  const onClearPreset = () => { setOpenTables((prev) => new Set(prev).add(key)); clearRelated(); };
                  return <DataExplorer key={asset.href} href={asset.href} presetFilter={preset} onClearPreset={onClearPreset} />;
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

// A same-view link to another catalog entry (keeps the view and its other search keys). `terrain`
// is cleared because the 3D toggle belongs to the item it was set on; a newly opened one starts 2D.
function EntryLink({ c, i, className, children }: { c: string; i: string; className?: string; children: ReactNode }) {
  return (
    <Link to="." search={(prev) => ({ ...prev, c, i, terrain: undefined })} className={className}>
      {children}
    </Link>
  );
}

// A publication's layers and tables, one row per Item, titled from its rel=item links. Each opens
// at `?c=<series>/<SID>&i=<item>`. A report-only publication has none, so nothing renders.
function PublicationParts({ item, collectionId }: { item: StacDoc; collectionId: string }) {
  const parts = collectionParts(item);
  if (!parts.length || !collectionId || !item.id) return null;
  const c = `${collectionId}/${item.id}`;
  return (
    <section className="mt-4 rounded-md border border-border bg-card p-3">
      <h3 className="text-sm font-semibold">Layers and tables</h3>
      <ul className="mt-1.5 divide-y divide-border text-sm">
        {parts.map((p) => (
          <li key={p.id} className="py-1">
            <EntryLink c={c} i={p.id} className="text-primary no-underline hover:underline">{p.title} ›</EntryLink>
          </li>
        ))}
      </ul>
    </section>
  );
}

export type PartOf = { title: string; c: string; i: string };

function PartOfLine({ partOf }: { partOf?: PartOf }) {
  if (!partOf) return null;
  return (
    <p className="mt-1 text-sm text-muted-foreground">
      Part of <EntryLink c={partOf.c} i={partOf.i} className="text-primary hover:underline">{partOf.title}</EntryLink>
    </p>
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

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section aria-label={title} className="space-y-3">
      <h2 className="text-base font-semibold tracking-tight text-foreground">{title}</h2>
      {children}
    </section>
  );
}

// One labeled metadata group (Curated / Derived) as a key→value list; hidden when it has no rows.
// The derived metadata a reader cares about; the pipeline's own fields (dbt schema, serving table,
// keys) stay in the STAC JSON.
const GLANCE = new Set(["Records", "Mappable", "Coordinate system", "Ingested"]);
const PRIMARY = "inline-flex items-center rounded border border-primary bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground no-underline hover:opacity-90";
const SECONDARY = "inline-flex items-center rounded border border-border bg-card px-3 py-1.5 text-sm font-medium text-foreground no-underline hover:border-primary";

function MetaList({ rows }: { rows: MetaRow[] }) {
  return (
    <dl className="divide-y divide-border overflow-hidden rounded-lg border border-border">
      {rows.map((r) => (
        // flex-wrap: a long value (a paragraph of lineage) drops under its label.
        <div key={r.label} className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 bg-card px-3 py-2 text-sm">
          <dt className="text-muted-foreground">{r.label}</dt>
          <dd className="min-w-0 break-words font-medium text-foreground">{r.value}</dd>
        </div>
      ))}
    </dl>
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

export function ItemDetail({ collectionId, item, raw, error, missing, partOf, crumbs, onBack, onMap, onExplore, layout = "drawer" }: {
  collectionId: string; item?: StacDoc; onBack: () => void; onMap: () => void;
  raw?: StacDoc;                   // the doc as published, for the JSON panel (item may be normalised)
  error?: unknown;                 // the item request's error, so a failure shows as one, not as loading
  missing?: string;                // an id this catalog does not have: say so instead of loading
  partOf?: PartOf;                 // the publication an Item belongs to
  crumbs?: { label: string; onClick?: () => void }[];  // a full trail, replacing `collection / id`
  onExplore?: () => void;          // full-screen Preview (offered in the Discover drawer)
  layout?: "page" | "drawer";      // page = full-width 2/3·1/3 grid; drawer = single column
}) {
  if (missing) {
    return (
      <div role="status" className="space-y-2 text-sm">
        <p className="text-foreground">Not found: <code>{missing}</code> is not in this catalog.</p>
        <button type="button" onClick={onBack} className="text-primary hover:underline">
          ‹ Back to {collectionId ? collectionLabel(collectionId) : "the catalog"}
        </button>
      </div>
    );
  }
  if (!item) {
    return error
      ? <Unavailable what="this item" error={error}
          fallback="Could not load this item." />
      : <em className={C.muted}>Loading…</em>;
  }
  const p = item.properties ?? {};
  const hasGeom = hasFootprint(item);
  const via = viaLink(item);
  const cite = citeLink(item);

  const crumb = crumbs ? (
    <div className="mb-1.5 text-xs">
      {crumbs.map((c, i) => (
        <span key={i}>
          {i > 0 && <span className={C.muted}> / </span>}
          {c.onClick ? <span className={C.crumb} onClick={c.onClick}>{c.label}</span>
            : <span className={C.muted}>{c.label}</span>}
        </span>
      ))}
    </div>
  ) : (
    <div className="mb-1.5 text-xs">
      <span className={C.crumb} onClick={onBack}>{collectionId}</span>
      <span className={C.muted}> / {item.id}</span>
    </div>
  );

  // The item-view getters read an ItemRef; build a lightweight one over the resolved doc.
  const it: ItemRef = { collId: collectionId, href: "", data: item };
  const meta = curatedDerived(p);
  const glance = meta.derived.filter((r) => GLANCE.has(r.label));
  const facts = [categorize(it).label, kindLabel(it), recordCountLabel(it), ...bylineParts(it)].filter(Boolean);
  const page = layout === "page";
  const glanceRows = [...glance, ...meta.curated, ...aboutRows(p)];
  const atAGlance = glanceRows.length > 0 && <Section title="At a glance"><MetaList rows={glanceRows} /></Section>;
  const description = typeof p.description === "string" && p.description;
  // Title, facts, description and actions, shared by both layouts.
  const head = (
    <>
      {crumb}
      {/* A div, not <header>: at the top level a <header> is a second banner landmark beside the site's. */}
      <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
        <div className="min-w-0 max-w-3xl">
          <h1 className="font-display text-2xl font-semibold tracking-tight text-foreground sm:text-3xl">{String(p.title ?? item.id ?? "")}</h1>
          <PartOfLine partOf={partOf} />
          <p className="mt-1.5 text-sm text-muted-foreground">
            {facts.join(" · ")}
            {IS_REVIEW && item.id && <> <ReviewBadge itemId={String(item.id)} /></>}
          </p>
          {!page && description && <p className="mt-3 text-muted-foreground">{description}</p>}
        </div>
        <div className="flex flex-wrap gap-2">
          {/* Phones only: from lg up the Downloads list is already in view beside the map. */}
          {page && listedAssets(item).length > 0 && <a href="#downloads" className={`${PRIMARY} lg:hidden`}>Download</a>}
          {hasGeom && <button onClick={onMap} className={SECONDARY}>View on map</button>}
          <AddToMapButton large />
          <SaveLayerOffline item={item} className={SECONDARY} />
          {onExplore && <button onClick={onExplore} className={SECONDARY}>Explore ⤢</button>}
          {via && <a href={via.href} target="_blank" rel="noopener" className={SECONDARY}>{via.title ?? "Publication page"} ↗</a>}
          {cite && <a href={cite.href} target="_blank" rel="noopener" className={SECONDARY}>Cite (DOI) ↗</a>}
        </div>
      </div>
      {/* Same grid as the map below, so the description wraps at the map's width. */}
      {page && description && (
        <div className="mt-3 grid gap-8 lg:grid-cols-3">
          <p className="min-w-0 text-muted-foreground lg:col-span-2">{description}</p>
        </div>
      )}
    </>
  );

  if (page) {
    // A vector layer lists its fields under the map; anything else lists them here.
    const fieldsUnderMap = Boolean(pmtilesLink(item));
    return (
      // No outer padding — the caller (Browse) already wraps this in C.wrap.
      <>
        {head}
        {/* min-w-0 on the grid CHILDREN, not a width on anything: a grid item defaults to
            min-width:auto, so the single mobile column sized itself to its widest descendant's
            max-content (861px inside a 360px phone) and everything below inherited that. */}
        <CubeStepsProvider key={String(item.id)}>
        <div className="mt-5 grid gap-8 lg:grid-cols-3">
          <div className="min-w-0 space-y-6 lg:col-span-2">
            <Preview item={item} />
            <IssueContents item={item} />
            <PublicationParts item={item} collectionId={collectionId} />
            {!fieldsUnderMap && <FieldsPanel item={item} />}
            <RelatedPanel item={item} />
            {IS_REVIEW && <CatalogReview item={item} />}
          </div>
          <aside className="min-w-0 space-y-6">
            {atAGlance}
            <div id="downloads" className="scroll-mt-24 space-y-6">
              <AssetsPanel item={item} />
              <CubeStepsPanel />
              <DownloadsPanel key={String(item.id)} item={item} />
            </div>
            <EndpointsPanel item={item} />
            <StacJson item={raw ?? item} title={`${item.id} — STAC JSON`} />
          </aside>
        </div>
        </CubeStepsProvider>
      </>
    );
  }

  // drawer: a short look that fits the Discover drawer. Downloads and links are on the full page.
  return (
    <div className="space-y-5">
      {head}
      <Preview item={item} />
      <IssueContents item={item} />
      <PublicationParts item={item} collectionId={collectionId} />
      {atAGlance}
      {IS_REVIEW && <CatalogReview item={item} />}
    </div>
  );
}

