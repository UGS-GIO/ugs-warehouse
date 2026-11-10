// The preview on a publication page with map layers: the plate and the layers drawn together in
// one Map preview, with an "On this preview" panel to turn each on or off.
import { useMemo, useState } from "react";

import { unitsHrefOf } from "@/map/identify";
import { colorForId } from "@/map/map-model";
import { PreviewMapSlot } from "@/map/preview-map";
import type { PreviewSpec } from "@/map/preview-spec";
import { type CollectionPart, collectionParts, describeData, parquetAsset, partHrefsOf, pmtilesLink,
  type StacDoc, useDocs, usePlate, useStac } from "@/stac";
import { DataExplorer } from "@/data/data-explorer";
import { assetPreviewEntry, IdentifyResultCard, type PreviewEntry, PreviewTabs } from "./asset-viewer";
import { type AssetEntry, assetEntries, drawOrder, isCartographic, isMapLayer, listOrder, plateEntry,
  TAB_LABEL } from "./item-view";

export const hasMapLayers = (item: StacDoc | undefined): boolean => collectionParts(item).some(isMapLayer);

// The publication's own previewable files, by tab label (Document, Image).
const fileEntries = (item: StacDoc): AssetEntry[] =>
  assetEntries(item).filter((e) => e.kind !== "other" && e.kind !== "cog");

const partFormat = (p: CollectionPart) =>
  [p.kind === "layer" ? "PMTiles + GeoParquet" : "GeoParquet", describeData({ geometry: p.geometry, table: p.kind !== "layer", rows: p.rows })]
    .filter(Boolean).join(" · ");

// A table of the publication, fetched by its Item, in the paged DuckDB explorer.
function PartTable({ href, part }: { href?: string; part: CollectionPart }) {
  const q = useStac(href);
  const pq = q.data && parquetAsset(q.data);
  if (!href) return <p role="alert" className="mt-2 text-xs text-destructive">{part.title} has no link in this publication.</p>;
  if (q.error) return <p role="alert" className="mt-2 text-xs text-destructive">Could not load {part.title}: {String(q.error)}</p>;
  if (!q.data) return <p className="mt-2 text-xs text-muted-foreground">Loading {part.title}…</p>;
  if (!pq) return <p role="alert" className="mt-2 text-xs text-destructive">{part.title} has no GeoParquet file.</p>;
  return <div className="mt-2"><DataExplorer key={pq.href} href={pq.href} title={part.title} /></div>;
}

// What the Map preview shows. Held by PublicationPreview, so it outlives a switch to another tab.
type Shown = { on: Record<string, boolean>; plateOn: boolean };

// The plate under the publication's layers, bottom first. Geology starts on, cartography off.
function SetMap({ item, plate, hrefs, shown, setShown }: {
  item: StacDoc; plate?: StacDoc; hrefs: Map<string, string>;
  shown: Shown; setShown: (f: (s: Shown) => Shown) => void;
}) {
  const parts = collectionParts(item);
  const layers = [...drawOrder(parts)].reverse();
  const urls = layers.map((p) => hrefs.get(p.id));
  const { docs, error } = useDocs(urls.filter((u): u is string => !!u));
  const docOf = new Map(docs.map((d) => [d.url, d.data]));
  const isOn = (p: CollectionPart) => shown.on[p.id] ?? !isCartographic(p);
  const setOn = (ps: CollectionPart[], v: boolean) =>
    setShown((s) => ({ ...s, on: { ...s.on, ...Object.fromEntries(ps.map((p) => [p.id, v])) } }));
  const cog = plateEntry(plate)?.asset;
  const unitsHref = unitsHrefOf(plate);
  const drawn = layers.flatMap((p, i) => {
    const pm = pmtilesLink(urls[i] ? docOf.get(urls[i]) : undefined);
    return pm ? [{ id: p.id, pmHref: pm.href, sourceLayer: pm["pmtiles:layers"]?.[0] ?? p.id, color: colorForId(p.id), on: isOn(p) }] : [];
  });
  // useDocs hands back new objects each render; the spec only changes when what it draws does.
  const sig = JSON.stringify([cog?.href, unitsHref, shown.plateOn, drawn]);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const spec: PreviewSpec = useMemo(() => ({ kind: "set", item, cogHref: cog?.href, unitsHref, plateOn: shown.plateOn, layers: drawn }), [sig, item]);
  const geology = listOrder(parts).filter((p) => isMapLayer(p) && !isCartographic(p));
  const carto = listOrder(parts).filter((p) => isMapLayer(p) && isCartographic(p));
  const row = (p: CollectionPart) => (
    <li key={p.id}>
      <label className="flex cursor-pointer items-center gap-1.5 py-0.5">
        <input type="checkbox" checked={isOn(p)} onChange={(e) => setOn([p], e.target.checked)} />
        <span aria-hidden className="inline-block size-2.5 shrink-0 rounded-sm" style={{ background: colorForId(p.id) }} />
        <span className="truncate">{p.title}</span>
      </label>
    </li>
  );
  const groupBox = (label: string, ps: CollectionPart[]) => {
    const all = ps.every(isOn);
    return <input type="checkbox" checked={all} onChange={() => setOn(ps, !all)} aria-label={`Show all ${label}`} />;
  };
  return (
    <>
      <div className="relative">
        <PreviewMapSlot spec={spec} />
        <details open className="absolute left-2 top-4 z-10 max-h-[calc(100%-2rem)] w-60 overflow-auto rounded-md border border-border bg-card/95 p-2 text-xs shadow backdrop-blur-sm">
          <summary className="cursor-pointer font-semibold text-foreground">On this preview</summary>
          <div className="mt-1.5 space-y-2">
            {cog && (
              <label className="flex cursor-pointer items-center gap-1.5 font-semibold text-foreground">
                <input type="checkbox" checked={shown.plateOn} onChange={(e) => setShown((s) => ({ ...s, plateOn: e.target.checked }))} />
                Map plate
              </label>
            )}
            {geology.length > 0 && (
              <div>
                <div className="flex items-center gap-1.5 font-semibold text-foreground">
                  {groupBox("Geology", geology)}Geology <span className="font-normal text-muted-foreground">· {geology.length}</span>
                </div>
                <ul className="pl-4">{geology.map(row)}</ul>
              </div>
            )}
            {carto.length > 0 && (
              <div className="flex items-start gap-1.5">
                {groupBox("Cartographic and labels", carto)}
                <details className="min-w-0 flex-1">
                  <summary className="cursor-pointer font-semibold text-foreground">
                    Cartographic and labels <span className="font-normal text-muted-foreground">· {carto.length}</span>
                  </summary>
                  <ul>{carto.map(row)}</ul>
                </details>
              </div>
            )}
          </div>
        </details>
      </div>
      {error != null && <p role="alert" className="mt-1 text-xs text-destructive">Some layers could not be loaded: {String(error)}</p>}
      <p className="mt-1 text-xs text-muted-foreground">Click a unit on the plate to see what it is.</p>
      <IdentifyResultCard />
    </>
  );
}

// Keyed by the publication where it is used, so what is shown resets on another publication.
export function PublicationPreview({ item }: { item: StacDoc }) {
  const { plate, error } = usePlate(item);
  const [shown, setShown] = useState<Shown>({ on: {}, plateOn: true });
  const hrefs = useMemo(() => partHrefsOf(item), [item]);
  const parts = collectionParts(item);
  const files = fileEntries(item);
  const tables = listOrder(parts).filter((p) => p.kind && !isMapLayer(p));
  const layerCount = parts.filter(isMapLayer).length;
  const byLabel = (label: string) => files.filter((e) => TAB_LABEL[e.kind] === label).map((e) => assetPreviewEntry(e, item));
  const tabs = [
    { label: "Document", entries: byLabel("Document") },
    { label: "Map preview", entries: [{ key: "set", title: `Map plate and ${layerCount} map layers`,
        format: "Cloud-Optimized GeoTIFF + PMTiles",
        render: () => <SetMap item={item} plate={plate} hrefs={hrefs} shown={shown} setShown={setShown} /> }] },
    { label: "Table", entries: tables.map((p): PreviewEntry => ({ key: p.id, title: p.title, format: partFormat(p),
        render: () => <PartTable key={p.id} href={hrefs.get(p.id)} part={p} /> })) },
    { label: "Image", entries: byLabel("Image") },
  ].filter((t) => t.entries.length);
  return (
    <>
      <PreviewTabs tabs={tabs} initial="Map preview" />
      {error != null && (
        <div role="alert" className="mt-2 text-xs text-destructive">
          Map plate could not be loaded: {error instanceof Error ? error.message : String(error)}
        </div>
      )}
    </>
  );
}
