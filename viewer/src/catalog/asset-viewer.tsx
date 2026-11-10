// Per-asset preview: picks a viewer by asset kind (map, datacube, 3D, PDF, table, image, text).
import { SchemaTable } from "./schema-table";
import { Tabs } from "@base-ui/react/tabs";
import { useQuery } from "@tanstack/react-query";
import { qk } from "@/query-keys";
import { lazy, type ReactNode, Suspense, useMemo, useRef, useState } from "react";

import { useScrollOnNew } from "@/lib/use-scroll-on-new";
import { DataExplorer } from "@/data/data-explorer";
import { FeatureCard } from "@/map/feature-card";
import { unitsHrefOf } from "@/map/identify";
import { IdentifyCard } from "@/map/unit-card";
import { footprintSpecOf, PreviewMapSlot, usePreviewMap } from "@/map/preview-map";
import { vectorSpecOf } from "@/map/preview-spec";
import { type Asset, type AssetKind, cogRenderAsset, geopackageAsset, isDrawableCog, parquetAsset, primaryKeyOf, rasterTilesAsset, type StacDoc,
  summaryFieldsOf, tableColumns, thumbnailAsset, usePlate } from "@/stac";
import { C, toggle } from "@/ui/ui";
import { UiSegmented } from "@/ui/segmented";
import { UiSelect } from "@/ui/select";
import { useDataSaver } from "@/lib/data-saver";
import { type AssetEntry, assetEntries, formatName, plateEntry, previewKindOf, TAB_LABEL, tabGroups } from "./item-view";
import { Chevron } from "@/ui/chevron";

// deck.gl's mesh layers and the terrain builder: only items with a 3D asset need them, so they load
// with the first one rather than with every catalog page.
const ThreeDViewer = lazy(() => import("@/data/three-d-viewer").then((m) => ({ default: m.ThreeDViewer })));

// deck.gl-zarr drags in luma.gl + the reprojection stack; only datacube items pay for it.
const ZarrMap = lazy(() => import("@/zarr/zarr-map").then((m) => ({ default: m.ZarrMap })));

export function AssetChips({ assets }: { assets: Record<string, Asset> }) {
  return (
    <>
      {Object.entries(assets).map(([k, a]) => (
        <a key={k} className={C.chip} href={a.href} target="_blank" rel="noopener"
          onClick={(e) => e.stopPropagation()}>{a.title ?? k}</a>
      ))}
    </>
  );
}

// The one field list: under the map on a layer, on the page for a table with no map. Collapsed, so a
// long schema does not push the data below it off the page.
export function FieldsPanel({ item }: { item: StacDoc }) {
  const cols = tableColumns(item);
  if (!cols) return null;
  return (
    <details className="group mt-3 text-xs">
      <summary className="inline-flex cursor-pointer list-none items-center gap-1.5 text-xs font-medium text-muted-foreground hover:text-foreground">
        <Chevron />
        Fields <span className="font-normal">· {cols.length}</span>
      </summary>
      <div className="mt-2"><SchemaTable columns={cols} /></div>
    </details>
  );
}

// Raster mosaic (per-scale geologic-map tiles) preview — publishes a spec to the shared persistent map.
// A tier baked as a COG too can switch between the two.
function RasterMosaicPreview({ item }: { item: StacDoc }) {
  const pm = rasterTilesAsset(item);
  const cog = cogRenderAsset(item);
  const [source, setSource] = useState<"rasterpm" | "cog">("rasterpm");
  const kind = cog && (source === "cog" || !pm) ? "cog" : "rasterpm";
  const asset = kind === "cog" ? cog : pm;
  return (
    <>
      {pm && cog && (
        <UiSegmented className="mb-2 w-fit" value={kind} onValueChange={setSource}
          items={[{ value: "rasterpm", label: "PMTiles" }, { value: "cog", label: "COG" }]} />
      )}
      <PreviewMapSlot spec={asset ? { kind, item, href: asset.href, unitsHref: unitsHrefOf(item) } : null} />
      <IdentifyResultCard />
    </>
  );
}

// Directly under the preview map, above the fields/table, so a click's result is in view without
// scrolling past a long data table.
function SelectedFeatureCard({ item }: { item: StacDoc }) {
  const { selectedFeature, clearSelection, openRelated, setFocus } = usePreviewMap();
  const ref = useRef<HTMLDivElement>(null);
  // `selectFeature` builds a new object per click, so clicking the same feature twice re-scrolls.
  useScrollOnNew(selectedFeature, ref);
  if (!selectedFeature) return null;
  const { props, fid, bbox } = selectedFeature;
  // A new key each time, so a second Zoom to flies again after the user has panned away.
  const zoom = bbox && (() => setFocus({ bbox, featureId: fid ?? undefined, props, key: Date.now() }));
  return <div ref={ref}><FeatureCard item={item} props={props} onOpenRelated={openRelated} onClear={clearSelection} onZoom={zoom} /></div>;
}

// A click on a plate or mosaic preview: the unit under it, or the scan-only notice.
export function IdentifyResultCard() {
  const { identify, clearIdentify } = usePreviewMap();
  return identify ? <IdentifyCard result={identify} onClose={clearIdentify} /> : null;
}

// Vector asset preview: the item's PMTiles on the shared persistent map + full dataset explorer,
// linked — click a table row → map flies to that feature; click a map feature → table pages to it,
// and its detail docks in the card directly below the map. The map instance lives in
// PreviewMapProvider (mounted once); this publishes the vector spec and wires the table↔map state.
function VectorPreview({ item }: { item: StacDoc }) {
  const pq = parquetAsset(item);
  const { setFocus, pick, mapMismatch } = usePreviewMap();
  const spec = vectorSpecOf(item);
  return (
    <>
      <PreviewMapSlot spec={spec} />
      <SelectedFeatureCard item={item} />
      <FieldsPanel item={item} />
      {pq && <DataExplorer key={pq.href} href={pq.href} title={String(item.properties?.title ?? item.id ?? "")} startCollapsed onPick={setFocus} mapPick={pick} mapMismatch={mapMismatch} reviewItemId={String(item.id ?? "")}
        rowKey={primaryKeyOf(item)} summaryFields={summaryFieldsOf(item)} />}
    </>
  );
}

// Small text/CSV peek — fetch the head of the file and show it; no parsing, just a glance.
function TextPreview({ href }: { href: string }) {
  // Slice to 20k in the queryFn so only the preview is retained, not the whole (possibly large) file.
  const { data: txt, error } = useQuery({
    queryKey: qk.textPreview(href),
    queryFn: async ({ signal }) => (await (await fetch(href, { signal })).text()).slice(0, 20000),
  });
  if (error) return <div className="mt-2 text-xs text-destructive">preview failed: {error instanceof Error ? error.message : String(error)}</div>;
  if (txt === undefined) return <div className="mt-2 text-xs text-muted-foreground">loading…</div>;
  return (
    <pre className="mt-2 max-h-150 max-w-full overflow-auto rounded-md border border-border bg-muted p-3 text-xs leading-snug">
      {txt}{txt.length >= 20000 ? "\n… (truncated — open or download for the full file)" : ""}
    </pre>
  );
}

// ---- Interactive 3D Fence Diagram Viewer (deck.gl SolidPolygon/Path layers over maplibre 3D) ----
function AssetPane({ kind, asset, item }: { kind: AssetKind; asset: Asset; item: StacDoc }) {
  switch (kind) {
    case "cog":
      // A COG in the source projection has no preview here: the renderer cannot reproject, and
      // mounting it throws. Say so, and leave the download to the asset link.
      return isDrawableCog(asset, item)
        ? <><PreviewMapSlot spec={{ kind: "cog", item, href: asset.href, unitsHref: unitsHrefOf(item) }} /><IdentifyResultCard /></>
        : (
          <p className="mt-2 rounded-md border border-border bg-muted px-3 py-2 text-xs text-muted-foreground">
            No preview: this is the source raster, in its own projection. The Web Mercator copy on
            this item previews here; this one is for download and desktop GIS.
          </p>
        );
    case "zarr":
      return (
        <Suspense fallback={<div className="mt-2 h-96 w-full animate-pulse rounded-md border border-border bg-muted" />}>
          <ZarrMap asset={asset} item={item} />
        </Suspense>
      );
    case "threeD": return <Suspense fallback={<p className="text-sm text-muted-foreground">Loading the 3D viewer…</p>}><ThreeDViewer asset={asset} item={item} /></Suspense>;
    case "parquet": return <DataExplorer key={asset.href} href={asset.href} />;
    case "image":
      return (
        <img src={asset.href} alt={asset.title ?? "image"} loading="lazy"
          className="mt-2 max-h-150 w-auto max-w-full rounded-md border border-border bg-muted object-contain" />
      );
    case "pdf": return <PdfPreview asset={asset} item={item} />;
    case "text": return <TextPreview href={asset.href} />;
    default:
      return (
        <div className="mt-2 rounded-md border border-border bg-muted p-3 text-xs text-muted-foreground">
          No in-page preview for this file type. <a href={asset.href} target="_blank" rel="noopener" className="text-primary">Download / open ↗</a>
        </div>
      );
  }
}

// PDF preview is click-to-load: the cover thumbnail shows instantly as a poster, and the (often
// 50–70MB, cross-origin) PDF only embeds when asked. Avoids a heavy auto-download + a blank box
// while a big file streams in. The cover + open-in-tab link always work regardless.
function PdfPreview({ asset, item }: { asset: Asset; item: StacDoc }) {
  const saver = useDataSaver();   // data saver: no images the person did not ask for
  const [show, setShow] = useState(false);
  const poster = thumbnailAsset(item)?.href;
  if (show) {
    return (
      <object data={asset.href} type="application/pdf"
        className="mt-2 h-100 w-full rounded-md border border-border sm:h-160">
        <div className="p-3 text-xs text-muted-foreground">
          Can’t embed this PDF — <a href={asset.href} target="_blank" rel="noopener" className="text-primary">open it ↗</a>
        </div>
      </object>
    );
  }
  return (
    <div className="mt-2">
      <button onClick={() => setShow(true)} title="Load the full PDF preview"
        className="group relative block w-full overflow-hidden rounded-md border border-border bg-muted">
        {poster && !saver
          ? <img src={poster} alt={asset.title ?? "PDF cover"} className="max-h-160 w-full object-contain" />
          : <div className="flex h-64 items-center justify-center text-xs text-muted-foreground">PDF</div>}
        <span className="absolute inset-0 flex items-center justify-center bg-black/0 transition group-hover:bg-black/20">
          <span className="rounded-md bg-primary px-3 py-1.5 text-sm text-primary-foreground shadow">View PDF ▸</span>
        </span>
      </button>
      <p className="mt-1 text-xs text-muted-foreground">
        Large file — loads on click. Or <a href={asset.href} target="_blank" rel="noopener" className="text-primary hover:underline">open in a new tab ↗</a>.
      </p>
    </div>
  );
}

// One thing a preview tab can show: its title and format make the caption under the tabs.
export type PreviewEntry = { key: string; title: string; format?: string; render: () => ReactNode };
type PreviewTab = { label: string; entries: PreviewEntry[] };

// Tabs named from the fixed vocabulary (TAB_LABEL). A tab holding several files (two images) picks
// between them in its caption; the caption says which file is shown and its format.
export function PreviewTabs({ tabs, initial }: { tabs: PreviewTab[]; initial?: string }) {
  // No reset-on-item-change effect: an unknown remembered tab or entry falls back to the first.
  const [tabLabel, setTabLabel] = useState(initial);
  const [picked, setPicked] = useState<Record<string, string>>({});
  const tab = tabs.find((t) => t.label === tabLabel) ?? tabs[0];
  if (!tab) return null;
  const entry = tab.entries.find((e) => e.key === picked[tab.label]) ?? tab.entries[0];
  const pick = (k: string) => setPicked((p) => ({ ...p, [tab.label]: k }));
  const choices = tab.entries.map((e) => ({ value: e.key, label: e.title }));
  return (
    <Tabs.Root value={tab.label} onValueChange={(v) => setTabLabel(String(v))} className="mt-2">
      {tabs.length > 1 && (
        <Tabs.List aria-label="Preview" className="mb-1.5 flex flex-wrap gap-1.5">
          {tabs.map((t) => (
            <Tabs.Tab key={t.label} value={t.label} className={(s) => toggle(s.active) + " rounded"}
              aria-label={t.entries.length > 1 ? `${t.label}, ${t.entries.length}` : undefined}>
              {t.label}
              {t.entries.length > 1 && <span aria-hidden className="ml-1 text-muted-foreground">{t.entries.length}</span>}
            </Tabs.Tab>
          ))}
        </Tabs.List>
      )}
      <Tabs.Panel value={tab.label}>
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
          {tab.entries.length > 3
            ? <UiSelect value={entry.key} onValueChange={pick} items={choices} label={`Which ${tab.label.toLowerCase()}`} className="min-w-48 text-xs" />
            : tab.entries.length > 1
            ? <UiSegmented value={entry.key} onValueChange={pick} items={choices} className="text-xs" />
            : <span className="font-medium text-foreground">{entry.title}</span>}
          {entry.format && <span>· {entry.format}</span>}
        </div>
        {entry.render()}
      </Tabs.Panel>
    </Tabs.Root>
  );
}

// An asset as a preview entry, its pane read with `doc` (the plate Item for the plate).
export const assetPreviewEntry = (e: AssetEntry, item: StacDoc): PreviewEntry => ({
  key: e.key, title: e.asset.title ?? e.key, format: formatName(e.asset, e.kind),
  render: () => <AssetPane kind={e.kind} asset={e.asset} item={e.doc ?? item} />,
});

// Tabbed viewer over every asset on an item: previewable files (PDF, image, COG, parquet, text)
// get a tab + inline pane; the rest are listed as download links. The default tab holds the
// highest-priority file (the publication PDF, else map > data > image > text).
function AssetViewer({ item, extra }: { item: StacDoc; extra?: AssetEntry }) {
  const entries = useMemo(() => assetEntries(item, extra), [item, extra]);
  const shown = entries.filter((e) => e.kind !== "other");
  // The GeoPackage has its own "Download all data" link on the publication page.
  const gpkg = geopackageAsset(item);
  const others = entries.filter((e) => e.kind === "other" && e.asset !== gpkg);

  if (!shown.length) {
    // Nothing previewable — show the footprint (if any) + download links for the raw files.
    return (
      <>
        <PreviewMapSlot spec={footprintSpecOf(item)} />
        {others.length > 0 && <div className="mt-2"><AssetChips assets={Object.fromEntries(others.map((e) => [e.key, e.asset]))} /></div>}
      </>
    );
  }
  const tabs = tabGroups(shown).map((g) => ({ label: g.label, entries: g.entries.map((e) => assetPreviewEntry(e, item)) }));
  return (
    <>
      <PreviewTabs tabs={tabs} initial={TAB_LABEL[shown[0].kind]} />
      {others.length > 0 && (
        <div className="mt-2 text-xs text-muted-foreground">
          Other files: <AssetChips assets={Object.fromEntries(others.map((e) => [e.key, e.asset]))} />
        </div>
      )}
    </>
  );
}

// Preview (previewKindOf): vector tiles (serving topics, publication layers) → interactive PMTiles
// map + linked dataset explorer; geologic map mosaics → interactive raster PMTiles map; everything
// else (publications, with their plate as a Map tab) → the tabbed asset viewer.
export function Preview({ item }: { item: StacDoc }) {
  const kind = previewKindOf(item);
  if (kind === "vector") return <VectorPreview item={item} />;
  if (kind === "mosaic") return <RasterMosaicPreview item={item} />;
  return <PlatedAssetViewer item={item} />;
}

// A publication's assets plus its plate's map, the PDF staying the default tab.
function PlatedAssetViewer({ item }: { item: StacDoc }) {
  const { plate, error } = usePlate(item);
  const extra = useMemo(() => plateEntry(plate), [plate]);
  return (
    <>
      <AssetViewer item={item} extra={extra} />
      {/* A plate that failed to load is said, not silently missing from the tabs. */}
      {error != null && (
        <div role="alert" className="mt-2 text-xs text-destructive">
          Map plate could not be loaded: {error instanceof Error ? error.message : String(error)}
        </div>
      )}
    </>
  );
}

// ---- API & data endpoints ----
