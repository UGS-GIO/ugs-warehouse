// Per-asset preview: picks a viewer by asset kind (map, datacube, 3D, PDF, table, image, text).
import { useQuery } from "@tanstack/react-query";
import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";

import { DataExplorer } from "@/data/data-explorer";
import { FeatureCard } from "@/map/feature-card";
import { footprintSpecOf, PreviewMapSlot, type PreviewSpec, usePreviewMap } from "@/map/preview-map";
import { type Asset, type AssetKind, assetKind, isDrawableCog, KIND_RANK, parquetAsset, pmtilesLink, primaryKeyOf, rasterTilesAsset, type StacDoc,
  summaryFieldsOf, tableColumns, thumbnailAsset } from "@/stac";
import { ThreeDViewer } from "@/data/three-d-viewer";
import { C, toggle } from "@/ui/ui";

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

function FieldsPanel({ item }: { item: StacDoc }) {
  const cols = tableColumns(item);
  if (!cols) return null;
  return (
    <details className="group mt-3 text-xs">
      <summary className="inline-flex cursor-pointer list-none items-baseline gap-1.5 text-xs font-medium text-muted-foreground hover:text-foreground">
        <span aria-hidden className="group-open:hidden">▸</span>
        <span aria-hidden className="hidden group-open:inline">▾</span>
        Fields <span className="font-normal">· {cols.length}</span>
      </summary>
      <div className="mt-2 flex flex-wrap gap-x-5 gap-y-1.5 rounded-md border border-border bg-card px-3 py-2.5">
        {cols.map((c) => (
          <span key={c.name} className="inline-flex items-baseline gap-1.5">
            <span className="font-mono text-foreground">{c.name}</span>
            {c.type && <span className="text-xs text-muted-foreground">{c.type}</span>}
          </span>
        ))}
      </div>
    </details>
  );
}

// Raster mosaic (per-scale geologic-map tiles) preview — publishes a spec to the shared persistent map.
function RasterMosaicPreview({ item }: { item: StacDoc }) {
  const asset = rasterTilesAsset(item);
  return <PreviewMapSlot spec={asset ? { kind: "rasterpm", item, href: asset.href } : null} />;
}

// A clicked map feature's detail, rendered directly under the preview map (above the fields/table) so
// it is in view the moment you click — not below a long data table. `nearest` keeps the map and the
// highlighted feature visible; gate on a genuinely new selection so a remount, or opening a related
// table, doesn't re-scroll. usePreviewMap gives the selection wired from the shared map.
function SelectedFeatureCard({ item }: { item: StacDoc }) {
  const { selectedFeature, clearSelection, openRelated } = usePreviewMap();
  const ref = useRef<HTMLDivElement>(null);
  const scrolledFor = useRef(selectedFeature);
  useEffect(() => {
    if (!selectedFeature || scrolledFor.current === selectedFeature) return;
    scrolledFor.current = selectedFeature;
    const t = setTimeout(() => ref.current?.scrollIntoView({ behavior: "smooth", block: "nearest" }), 0);
    return () => clearTimeout(t);
  }, [selectedFeature]);
  if (!selectedFeature) return null;
  return <div ref={ref}><FeatureCard item={item} props={selectedFeature.props} onOpenRelated={openRelated} onClear={clearSelection} /></div>;
}

// Vector asset preview: the item's PMTiles on the shared persistent map + full dataset explorer,
// linked — click a table row → map flies to that feature; click a map feature → table pages to it,
// and its detail docks in the card directly below the map. The map instance lives in
// PreviewMapProvider (mounted once); this publishes the vector spec and wires the table↔map state.
function VectorPreview({ item }: { item: StacDoc }) {
  const pq = parquetAsset(item);
  const pm = pmtilesLink(item);
  const { setFocus, pick } = usePreviewMap();
  const spec: PreviewSpec = pm
    ? { kind: "vector", item, pmHref: pm.href, sourceLayer: pm["pmtiles:layers"]?.[0] ?? String(item.id ?? "") }
    : null;
  return (
    <>
      <PreviewMapSlot spec={spec} />
      <SelectedFeatureCard item={item} />
      <FieldsPanel item={item} />
      {pq && <DataExplorer key={pq.href} href={pq.href} onPick={setFocus} mapPick={pick} reviewItemId={String(item.id ?? "")}
        rowKey={primaryKeyOf(item)} summaryFields={summaryFieldsOf(item)} />}
    </>
  );
}

const KIND_LABEL: Record<AssetKind, string> = {
  cog: "Map", zarr: "Datacube", threeD: "3D", pdf: "PDF", parquet: "Data", image: "Image", text: "Text", other: "File",
};


// Small text/CSV peek — fetch the head of the file and show it; no parsing, just a glance.
function TextPreview({ href }: { href: string }) {
  // Slice to 20k in the queryFn so only the preview is retained, not the whole (possibly large) file.
  const { data: txt, error } = useQuery({
    queryKey: ["text-preview", href],
    queryFn: async ({ signal }) => (await (await fetch(href, { signal })).text()).slice(0, 20000),
    staleTime: 5 * 60_000,
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
        ? <PreviewMapSlot spec={{ kind: "cog", item, href: asset.href }} />
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
    case "threeD": return <ThreeDViewer asset={asset} item={item} />;
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
        {poster
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

// Tabbed viewer over every asset on an item: previewable files (PDF, image, COG, parquet, text)
// get a tab + inline pane; the rest are listed as download links. The default tab is the
// highest-priority file (map > pdf > data > image > text).
function AssetViewer({ item }: { item: StacDoc }) {
  const entries = useMemo(() => Object.entries(item.assets ?? {})
    // thumbnails are redundant with the real image/cog; skip as their own tab
    .filter(([, a]) => !a.roles?.includes("thumbnail"))
    .map(([key, a]) => ({ key, asset: a, kind: assetKind(a) }))
    .sort((x, y) => (x.key === "publication" ? -1 : y.key === "publication" ? 1 : 0)
      || KIND_RANK[x.kind] - KIND_RANK[y.kind]
      // A raster item carries the canonical COG in its source projection AND a web-mercator
      // derivative. Both are kind "cog", so a stable sort left whichever came first as the default
      // tab — the native one, which this client cannot draw (warehouse#84).
      || Number(isDrawableCog(y.asset, item)) - Number(isDrawableCog(x.asset, item))), [item]);
  const tabs = entries.filter((e) => e.kind !== "other");
  const others = entries.filter((e) => e.kind === "other");
  // No reset-on-item-change effect: `active` below falls back to the first tab whenever the
  // remembered key isn't in this item's tabs.
  const [activeKey, setActiveKey] = useState<string | undefined>(tabs[0]?.key);

  if (!tabs.length) {
    // Nothing previewable — show the footprint (if any) + download links for the raw files.
    return (
      <>
        <PreviewMapSlot spec={footprintSpecOf(item)} />
        {others.length > 0 && <div className="mt-2"><AssetChips assets={Object.fromEntries(others.map((e) => [e.key, e.asset]))} /></div>}
      </>
    );
  }
  const active = tabs.find((e) => e.key === activeKey) ?? tabs[0];
  return (
    <div className="mt-2">
      {tabs.length > 1 && (
        <div className="mb-1.5 flex flex-wrap gap-1.5">
          {tabs.map((e) => (
            <button key={e.key} onClick={() => setActiveKey(e.key)}
              className={toggle(e.key === active.key) + " rounded"}>
              <span className="mr-1 text-muted-foreground">{KIND_LABEL[e.kind]}</span>
              {e.asset.title ?? e.key}
            </button>
          ))}
        </div>
      )}
      <AssetPane kind={active.kind} asset={active.asset} item={item} />
      {others.length > 0 && (
        <div className="mt-2 text-xs text-muted-foreground">
          Other files: <AssetChips assets={Object.fromEntries(others.map((e) => [e.key, e.asset]))} />
        </div>
      )}
    </div>
  );
}

// Preview: vector serving topics → interactive PMTiles map + linked dataset explorer;
// geologic map mosaics → interactive raster PMTiles map; everything else (publications) →
// the tabbed asset viewer so users can peruse every file in-page.
export function Preview({ item }: { item: StacDoc }) {
  if (pmtilesLink(item)) return <VectorPreview item={item} />;
  if (rasterTilesAsset(item)) return <RasterMosaicPreview item={item} />;
  return <AssetViewer item={item} />;
}

// ---- API & data endpoints ----
