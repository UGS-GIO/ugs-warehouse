// How to use an item from other software: its files on the CDN, and its links grouped by the
// software you paste them into (live services, desktop GIS, web maps, code, citation).
import { useEffect, useRef, useState } from "react";

import { featureEndpoints } from "./catalog";
import { Panel } from "./panel";
import {
  assetKind, citeLink, cogAsset, cogRenderAsset, featureServerUrl, FEATURES_BASE, geopackageAsset, imageServerUrl, isImageServed,
  isOverview, isServingTopic, mapViewerUrl, parquetAsset, pmtilesLink, rendersOf, type StacDoc, usePlate, viaLink,
} from "@/stac";
import { usePreviewMap } from "@/map/preview-map";
import { UiSelect } from "@/ui/select";

/** `…/thing.parquet?x=1` → `parquet` */
const extOf = (href: string) => href.split(/[?#]/)[0].match(/\.([a-z0-9]{1,8})$/i)?.[1].toLowerCase();

export function CopyBtn({ text, className = "px-1.5 py-0.5 text-sm" }: { text: string; className?: string }) {
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
      className={`rounded border border-border bg-card ${className} text-foreground hover:border-primary`}>
      {done ? "copied" : "copy"}
    </button>
  );
}

/**
 * Every file the item publishes, each under its own STAC title: the GeoParquet, the PMTiles, the
 * style, the metadata, a COG. The thumbnail is left out (it is the card picture), and so are
 * related tables, which the Related section offers, and the overviews only the FeatureServer reads.
 * A Zarr store is a prefix, so it is copied, not opened.
 */
export const listedAssets = (item: StacDoc) => Object.entries(item.assets ?? {})
  .filter(([, a]) => !a.roles?.includes("thumbnail") && !a.roles?.includes("related") && !isOverview(a));

export function AssetsPanel({ item }: { item: StacDoc }) {
  const assets = listedAssets(item);
  // A publication's plate COG lives on its plate Item; it is one of the publication's files too.
  const { plate } = usePlate(item);
  const plateCog = plate && (cogAsset(plate) ?? cogRenderAsset(plate));
  if (!assets.length && !plateCog) return null;
  const fileRow = (key: string, title: string, href: string, format?: string, zarr = false) => (
    <div key={key} className="flex items-center justify-between gap-2 px-3 py-2 text-sm">
      <span className="min-w-0 truncate font-medium text-foreground" title={href}>
        {title}
        <span className="ml-2 text-xs font-normal text-muted-foreground">{format}</span>
      </span>
      <span className="flex shrink-0 items-center gap-2">
        <CopyBtn text={href} />
        {!zarr && <a href={href} target="_blank" rel="noopener" className="text-primary no-underline" aria-label={`Download ${title}`}>↓</a>}
      </span>
    </div>
  );
  return (
    <Panel title="Files">
      <div className="divide-y divide-border overflow-hidden rounded-md border border-border bg-card">
        {plateCog && fileRow("plate", "Map plate", plateCog.href, "· Cloud-Optimized GeoTIFF")}
        {assets.map(([key, a]) => {
          const zarr = assetKind(a) === "zarr";
          return fileRow(key, a.title ?? key, a.href, zarr ? "zarr" : extOf(a.href), zarr);
        })}
      </div>
    </Panel>
  );
}

export function EndpointsPanel({ item, collectionId, href, parent }: {
  item: StacDoc; collectionId?: string;
  href?: string;       // the URL the doc was fetched from: its STAC JSON
  parent?: StacDoc;    // the publication a layer belongs to, for its all-layers GeoPackage
}) {
  const id = String(item.id ?? "");
  const pq = parquetAsset(item);
  const renders = Object.keys(rendersOf(item)).sort();
  // Follow the map's "Symbolize by" picker, so the URL you copy is the symbology on screen.
  const { render: shown } = usePreviewMap();
  const [pickedRender, setPickedRender] = useState<string>();
  // Explicit choice wins; otherwise track the map so the two never disagree silently.
  const chosen = [pickedRender, shown].find((r) => r && renders.includes(r)) ?? renders[0];
  const renderPick = renders.length > 1 ? (
    <UiSelect value={chosen} onValueChange={setPickedRender}
      title="Which published symbology this service serves"
      items={renders.map((r) => ({ value: r, label: r }))}
      className="max-w-44 shrink-0 text-sm" />
  ) : undefined;
  const { plate } = usePlate(item);
  type Row = { label: string; desc: string; url: string; pick?: React.ReactNode; esri?: boolean };
  const rows: Row[] = [];
  // Only an item with its own `rel=service` link is served (featureEndpoints).
  rows.push(...featureEndpoints(item, FEATURES_BASE));
  // The FeatureServer serves the serving-topics index's GeoParquet layers (isServingTopic). A
  // publication layer has a GeoParquet too, but is not served, so it gets no row.
  const fs = pq && isServingTopic(item) ? featureServerUrl(id, chosen) : undefined;
  if (fs) {
    rows.push({
      label: "ArcGIS feature layer",
      desc: "ArcGIS Online Map Viewer, Experience Builder, StoryMaps. Popups, tables and symbology included",
      url: fs,
      pick: renderPick,
      esri: true,
    });
  }
  const is = isImageServed(item, collectionId) ? imageServerUrl(id) : undefined;
  if (is) {
    rows.push({
      label: "ArcGIS image layer",
      desc: "ArcGIS Online Map Viewer, ArcGIS Pro, Experience Builder. The map image as a dynamic image service",
      url: is,
      esri: true,
    });
  }
  // Files to paste into desktop GIS. A publication's layers are not served, so ArcGIS Pro
  // takes the all-layers GeoPackage.
  const desktop: Row[] = [];
  const cog = cogAsset(item) ?? (plate && (cogAsset(plate) ?? cogRenderAsset(plate)));
  if (cog) desktop.push({ label: plate && !cogAsset(item) ? "Map plate (COG)" : "COG", desc: "The map image as a Cloud-Optimized GeoTIFF, read by URL", url: cog.href });
  if (pq) desktop.push({ label: "GeoParquet", desc: "The layer's features and attributes as GeoParquet", url: pq.href });
  const gpkg = geopackageAsset(item) ?? geopackageAsset(parent);
  if (gpkg) desktop.push({ label: "GeoPackage", desc: "Every layer and table of the publication in one file", url: gpkg.href });
  const web: Row[] = [];
  const pm = pmtilesLink(item);
  if (pm) web.push({ label: "PMTiles", desc: "Vector tiles for a web map, through the pmtiles protocol", url: pm.href });
  const style = item.assets?.style?.href ?? (chosen ? rendersOf(item)[chosen]?.style_url : undefined);
  if (style) web.push({ label: "Style", desc: "MapLibre GL style JSON", url: style });
  const code: Row[] = href ? [{ label: "STAC JSON", desc: "This entry in the STAC catalog, for STAC clients and scripts", url: href }] : [];
  const cite: Row[] = [];
  const doi = citeLink(item);
  if (doi) cite.push({ label: "DOI", desc: "The permanent link to cite", url: doi.href });
  const via = viaLink(item);
  if (via) cite.push({ label: "Landing page", desc: via.title ?? "The publication's page on geology.utah.gov", url: via.href });
  const groups: [string, Row[]][] = ([
    ["ArcGIS Online and services", rows], ["ArcGIS Pro / QGIS", desktop], ["Web maps", web],
    ["Code and catalogs", code], ["Cite and share", cite],
  ] as [string, Row[]][]).filter(([, g]) => g.length);
  if (!groups.length) return null;
  const row = (r: Row) => (
    <div key={r.label} className="flex flex-wrap items-center gap-2 text-sm">
      <span className="w-36 shrink-0 font-semibold text-foreground" title={r.desc}>{r.label}</span>
      {r.pick}
      {/* The narrow column cut every URL to "https://ugs-…", so it shows on hover only. */}
      <span className="min-w-0 flex-1" title={r.url} />
      <CopyBtn text={r.url} />
      <a href={r.url} target="_blank" rel="noopener" className="text-primary no-underline">open ↗</a>
      {r.esri && (
        <a href={mapViewerUrl(r.url)} target="_blank" rel="noopener" aria-label="Open in Map Viewer" title="Open in ArcGIS Online Map Viewer"
          className="text-primary no-underline">Map Viewer ↗</a>
      )}
    </div>
  );
  return (
    <div id="links" className="scroll-mt-24">
      <Panel title="Links">
        <div className="flex flex-col gap-3">
          {groups.map(([title, g]) => (
            <div key={title}>
              <div className="mb-1 text-xs uppercase tracking-wide text-muted-foreground">{title}</div>
              <div className="flex flex-col gap-1.5">{g.map(row)}</div>
            </div>
          ))}
        </div>
      </Panel>
    </div>
  );
}
