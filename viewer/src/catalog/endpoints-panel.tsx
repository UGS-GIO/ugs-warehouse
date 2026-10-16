// How to use an item from other software, in STAC's terms: its assets (the files on the CDN) and its
// links (live services: OGC API Features, ArcGIS FeatureServer).
import { useEffect, useRef, useState } from "react";

import { serviceUrlOf } from "./catalog";
import { Panel } from "./panel";
import { assetKind, featureServerUrl, parquetAsset, featuresCollectionUrl, FEATURES_BASE, rendersOf, type StacDoc } from "@/stac";
import { usePreviewMap } from "@/map/preview-map";
import { UiSelect } from "@/ui/select";

/** `…/thing.parquet?x=1` → `parquet` */
const extOf = (href: string) => href.split(/[?#]/)[0].match(/\.([a-z0-9]{1,8})$/i)?.[1].toLowerCase();

function CopyBtn({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  useEffect(() => () => clearTimeout(timer.current), []);  // clear a pending reset on unmount
  return (
    <button
      onClick={() => {
        navigator.clipboard?.writeText(text);
        setDone(true);
        clearTimeout(timer.current);
        timer.current = setTimeout(() => setDone(false), 1200);
      }}
      className="rounded border border-border bg-card px-1.5 py-0.5 text-sm text-foreground hover:border-primary">
      {done ? "copied" : "copy"}
    </button>
  );
}

/**
 * Every file the item publishes, each under its own STAC title: the GeoParquet, the PMTiles, the
 * style, the metadata, a COG. The thumbnail is left out (it is the card picture), and so are
 * related tables, which the Related section offers. A Zarr store is a prefix, so it is copied, not
 * opened.
 */
export const listedAssets = (item: StacDoc) => Object.entries(item.assets ?? {})
  .filter(([, a]) => !a.roles?.includes("thumbnail") && !a.roles?.includes("related"));

export function AssetsPanel({ item }: { item: StacDoc }) {
  const assets = listedAssets(item);
  if (!assets.length) return null;
  return (
    <Panel title="Assets">
      <div className="divide-y divide-border overflow-hidden rounded-md border border-border bg-card">
        {assets.map(([key, a]) => {
          const zarr = assetKind(a) === "zarr";
          return (
            <div key={key} className="flex items-center justify-between gap-2 px-3 py-2 text-sm">
              <span className="min-w-0 truncate font-medium text-foreground" title={a.href}>
                {a.title ?? key}
                <span className="ml-2 text-xs font-normal text-muted-foreground">{zarr ? "zarr" : extOf(a.href)}</span>
              </span>
              <span className="flex shrink-0 items-center gap-2">
                <CopyBtn text={a.href} />
                {!zarr && <a href={a.href} target="_blank" rel="noopener" className="text-primary no-underline" aria-label={`Download ${a.title ?? key}`}>↓</a>}
              </span>
            </div>
          );
        })}
      </div>
    </Panel>
  );
}

export function EndpointsPanel({ item }: { item: StacDoc }) {
  const id = String(item.id ?? "");
  const pq = parquetAsset(item);
  // What the catalog says beats what we can guess: the item's own `rel=service` link, else the
  // id template — and only for items with a GeoParquet, which is exactly what featureserv binds.
  // Raster and publication items have neither, so they get no row instead of a link to a
  // collection that never existed (#85: featureserv is keyed by item id, not collection id).
  const coll = serviceUrlOf(item, FEATURES_BASE) ?? (pq ? featuresCollectionUrl(id) : undefined);
  const esriRenders = Object.keys(rendersOf(item)).sort();
  // Follow the map's "Symbolize by" picker, so the URL you copy is the symbology on screen.
  const { render: shown } = usePreviewMap();
  const [pickedRender, setPickedRender] = useState<string>();
  // Explicit choice wins; otherwise track the map so the two never disagree silently.
  const chosen = [pickedRender, shown].find((r) => r && esriRenders.includes(r)) ?? esriRenders[0];
  const renderPick = esriRenders.length > 1 ? (
    <UiSelect value={chosen} onValueChange={setPickedRender}
      title="Which published symbology this service serves"
      items={esriRenders.map((r) => ({ value: r, label: r }))}
      className="max-w-[11rem] shrink-0 text-sm" />
  ) : undefined;
  type Row = { label: string; desc: string; url: string; pick?: React.ReactNode };
  const rows: Row[] = [];
  if (coll) {
    rows.push({ label: "OGC API Features", desc: "REST feature service — collection metadata", url: coll });
    rows.push({ label: "Features (GeoJSON)", desc: "Query features as GeoJSON (paged)", url: `${coll}/items?limit=50` });
  }
  const fs = pq ? featureServerUrl(id, chosen) : undefined;
  if (fs) {
    rows.push({
      label: "ArcGIS feature layer",
      desc: "ArcGIS Online Map Viewer, Experience Builder, StoryMaps. Popups, tables and symbology included",
      url: fs,
      pick: renderPick,
    });
  }
  if (!rows.length) return null;
  const row = (r: Row) => (
    <div key={r.label} className="flex flex-wrap items-center gap-2 text-sm">
      <span className="w-36 shrink-0 font-semibold text-foreground" title={r.desc}>{r.label}</span>
      {r.pick}
      {/* The narrow column cut every URL to "https://ugs-…", so it shows on hover only. */}
      <span className="min-w-0 flex-1" title={r.url} />
      <CopyBtn text={r.url} />
      <a href={r.url} target="_blank" rel="noopener" className="text-primary no-underline">open ↗</a>
    </div>
  );
  return (
    <Panel title="Links">
      <div className="flex flex-col gap-1.5">{rows.map(row)}</div>
    </Panel>
  );
}
