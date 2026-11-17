// The preview on a publication page with map layers: the plate and the layers drawn together in
// one Map preview, with an "On this preview" panel to turn each on or off.
import { useEffect, useMemo, useState } from "react";
import { useSearch } from "@tanstack/react-router";

import { unitsHrefOf } from "@/map/identify";
import { setHovered } from "@/map/highlight";
import { colorForId } from "@/map/map-model";
import { type PreviewHit, PreviewMapSlot, usePreviewMap } from "@/map/preview-map";
import type { PreviewSpec } from "@/map/preview-spec";
import { type CollectionPart, collectionParts, describeData, parquetAsset, partHrefsOf, pmtilesLink,
  type StacDoc, summaryFieldsOf, useDocs, usePlate, useStac } from "@/stac";
import { DataExplorer } from "@/data/data-explorer";
import { assetPreviewEntry, IdentifyResultCard, type PreviewEntry, PreviewTabs, SelectedFeatureCard } from "./asset-viewer";
import { EntryLink } from "./entry-link";
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

// Everything under a click, when there is more than one thing: hover a row to see it on the map,
// click it to show it below. The selected row is marked as on the map, in yellow.
const MAX_HITS = 8;
function SetHitList({ parts, docOf, shows }: {
  parts: CollectionPart[]; docOf: (id: string) => StacDoc | undefined;
  shows: (h: PreviewHit) => boolean;   // false for a hit on a layer since switched off
}) {
  const { hits: all, selectHit, selectedFeature, identify } = usePreviewMap();
  useEffect(() => () => setHovered(null), []);
  // The plate's unit leads, so a long list never hides it.
  const hits = (all ?? []).filter(shows).sort((a, b) => Number(!!b.unit) - Number(!!a.unit));
  if (hits.length < 2) return null;
  const isPicked = (h: PreviewHit) => (h.unit ? identify === h.unit : selectedFeature?.props === h.props);
  const label = (h: PreviewHit): { name: string; value?: string } => {
    if (h.unit?.kind === "unit") return { name: "Map plate", value: [h.unit.unit.code, h.unit.unit.name].filter(Boolean).join(" ") };
    const part = parts.find((p) => p.id === h.layerId);
    const key = [...summaryFieldsOf(h.layerId ? docOf(h.layerId) : undefined), ...Object.keys(h.props)]
      .find((k) => h.props[k] != null && h.props[k] !== "");
    return { name: part?.title ?? h.layerId ?? "Feature", value: key ? String(h.props[key]) : undefined };
  };
  return (
    <section aria-label="Under the click" className="mt-3 rounded-md border border-border bg-card p-2 text-xs">
      <div aria-live="polite" className="mb-1 px-1 font-semibold text-muted-foreground">{hits.length} things here</div>
      <ul onPointerLeave={() => setHovered(null)}>
        {hits.slice(0, MAX_HITS).map((h) => {
          const { name, value } = label(h);
          const picked = isPicked(h);
          return (
            <li key={h.key}>
              <button type="button" aria-current={picked || undefined} onClick={() => { setHovered(null); selectHit(h); }}
                onPointerEnter={(e) => { if (e.pointerType === "mouse" && !picked) setHovered(h.match ?? null); }}
                onFocus={() => { if (!picked) setHovered(h.match ?? null); }} onBlur={() => setHovered(null)}
                className={`flex w-full items-center gap-2 rounded border-l-4 px-2 py-1 text-left ${picked
                  ? "border-[#FFFF00] bg-muted" : "border-transparent hover:border-[#1E6BFF] hover:bg-hover"}`}>
                <span aria-hidden className="inline-block size-2.5 shrink-0 rounded-sm"
                  style={{ background: h.layerId ? colorForId(h.layerId) : "transparent", border: h.layerId ? undefined : "1px solid currentColor" }} />
                <span className="shrink-0 font-medium text-foreground">{name}</span>
                {value && <span className="min-w-0 truncate text-muted-foreground">{value}</span>}
              </button>
            </li>
          );
        })}
      </ul>
      {hits.length > MAX_HITS && <p className="px-1 pt-1 text-muted-foreground">and {hits.length - MAX_HITS} more; zoom in to narrow it down.</p>}
    </section>
  );
}

// The selected feature, under the name of its layer and a way to that layer's page.
function SetFeatureCard({ item, parts, docOf }: { item: StacDoc; parts: CollectionPart[]; docOf: (id: string) => StacDoc | undefined }) {
  const { selectedFeature } = usePreviewMap();
  const { c } = useSearch({ from: "__root__" });
  const part = selectedFeature?.layerId ? parts.find((p) => p.id === selectedFeature.layerId) : undefined;
  const doc = part && docOf(part.id);
  if (!part || !doc) return null;
  return (
    <div>
      <p className="mt-3 text-xs text-muted-foreground">
        From <span className="font-medium text-foreground">{part.title}</span>
        {c && item.id && <> · <EntryLink c={`${c}/${item.id}`} i={part.id} className="text-primary hover:underline">Open its page ›</EntryLink></>}
      </p>
      <SelectedFeatureCard item={doc} related={false} />
    </div>
  );
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
  const docById = (id: string) => { const u = hrefs.get(id); return u ? docOf.get(u) : undefined; };
  const isOn = (p: CollectionPart) => shown.on[p.id] ?? !isCartographic(p);
  const { selectedFeature, clearSelection, identify, clearIdentify } = usePreviewMap();
  const setOn = (ps: CollectionPart[], v: boolean) => {
    // A feature of a layer switched off is no longer on the map, so neither is its card.
    if (!v && ps.some((p) => p.id === selectedFeature?.layerId)) clearSelection();
    setShown((s) => ({ ...s, on: { ...s.on, ...Object.fromEntries(ps.map((p) => [p.id, v])) } }));
  };
  const cog = plateEntry(plate)?.asset;
  const unitsHref = unitsHrefOf(plate);
  const drawn = layers.flatMap((p, i) => {
    const pm = pmtilesLink(urls[i] ? docOf.get(urls[i]) : undefined);
    return pm ? [{ id: p.id, pmHref: pm.href, sourceLayer: pm["pmtiles:layers"]?.[0] ?? p.id, color: colorForId(p.id), on: isOn(p) }] : [];
  });
  // A layer with no link here, or whose loaded Item has no PMTiles, cannot be drawn: say so.
  const undrawable = new Set(layers.filter((p, i) => {
    const u = urls[i];
    return !u || (docOf.get(u) && !drawn.some((l) => l.id === p.id));
  }).map((p) => p.id));
  // useDocs hands back new objects each render; the spec only changes when what it draws does.
  const sig = JSON.stringify([cog?.href, unitsHref, shown.plateOn, drawn]);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const spec: PreviewSpec = useMemo(() => ({ kind: "set", item, cogHref: cog?.href, unitsHref, plateOn: shown.plateOn, layers: drawn }), [sig, item]);
  const mapLayers = listOrder(parts).filter(isMapLayer);
  const geology = mapLayers.filter((p) => !isCartographic(p));
  const carto = mapLayers.filter(isCartographic);
  const row = (p: CollectionPart) => (
    <li key={p.id}>
      <label className={`flex items-center gap-1.5 py-0.5 ${undrawable.has(p.id) ? "opacity-50" : "cursor-pointer"}`}>
        <input type="checkbox" checked={isOn(p) && !undrawable.has(p.id)} disabled={undrawable.has(p.id)}
          onChange={(e) => setOn([p], e.target.checked)} />
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
                <input type="checkbox" checked={shown.plateOn} onChange={(e) => {
                  if (!e.target.checked && identify) clearIdentify();
                  setShown((s) => ({ ...s, plateOn: e.target.checked }));
                }} />
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
      {undrawable.size > 0 && (
        <p role="alert" className="mt-1 text-xs text-destructive">
          Not drawn, no map tiles found: {layers.filter((p) => undrawable.has(p.id)).map((p) => p.title).join(", ")}
        </p>
      )}
      <p className="mt-1 text-xs text-muted-foreground">
        {cog && shown.plateOn ? "Click a feature, or a unit on the plate, to see what it is." : "Click a feature to see what it is."}
      </p>
      <SetHitList parts={layers} docOf={docById}
        shows={(h) => (h.unit ? shown.plateOn : !!parts.find((p) => p.id === h.layerId && isOn(p)))} />
      <SetFeatureCard item={item} parts={layers} docOf={docById} />
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
      {/* The document leads, as on every other publication; the Map preview is a click away. */}
      <PreviewTabs tabs={tabs} initial={tabs[0]?.label} />
      {error != null && (
        <div role="alert" className="mt-2 text-xs text-destructive">
          Map plate could not be loaded: {error instanceof Error ? error.message : String(error)}
        </div>
      )}
    </>
  );
}
