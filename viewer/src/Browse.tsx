// Catalog-centric browser: metadata over map. Collection cards (with counts) +
// search-all → sortable item table / cards → item detail. The map is one link out.
import {
  type ColumnDef, flexRender, getCoreRowModel, getSortedRowModel,
  type SortingState, useReactTable,
} from "@tanstack/react-table";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { COORDINATE_SYSTEM, OrbitView } from "@deck.gl/core";
import { PathStyleExtension } from "@deck.gl/extensions";
import { BitmapLayer, PathLayer, SolidPolygonLayer } from "@deck.gl/layers";
import { SimpleMeshLayer } from "@deck.gl/mesh-layers";
import DeckGL from "@deck.gl/react";
import { useEffect, useMemo, useRef, useState } from "react";
import { type ColFilter, exportItem, type ExportFormat, FORMATS, type ShapefileWarnings, shapefileWarnings } from "./download";
import { buildMeshFrom3DEP, type TerrainMesh } from "./terrain";
import { lruSet } from "./lru";
import { PreviewMapSlot, type PreviewSpec, usePreviewMap, footprintSpecOf } from "./PreviewMap";
import type { FocusSel } from "./map-model";
import { type Asset, citeLink, classificationColors, cogAsset, contentsOf, featuresCollectionUrl, IS_REVIEW, ownForeignKeys, pmtilesLink, primaryKeyOf, rasterTilesAsset, relatedAssets, relatedLinks, type StacDoc, tableColumns, thumbnailAsset, viaLink } from "./stac";
import { CommentsPanel } from "./CommentsPanel";
import { DiffPanel } from "./DiffPanel";
import { PhotoGallery } from "./PhotoGallery";
import { LayerStatusControl } from "./ReviewStatus";
import { createComment } from "./comments";

// STAC item id for an ItemRef — the loaded doc's id, else the folder stem from the href
// (…/<collection>/<id>/<id>.json). Matches ItemDetail's item.id + the warehouse item id.
const itemIdOf = (it: ItemRef): string =>
  String(it.data?.id ?? it.href.replace(/\/[^/]+\.json.*$/, "").split("/").pop() ?? it.href);

// A few latest covers for a collection card (thumbnail strip). `date` = the item datetime, used to
// merge + re-sort covers across series for a sub-catalog card. Populated by App from the indexes.
export type CoverRef = { href: string; thumb: string; title?: string; date?: string };
export type CollectionSummary = {
  id: string; href: string; title?: string; description?: string;
  count?: number; mappable?: number; kind?: "catalog" | "collection"; parentId?: string;
  covers?: CoverRef[];
};
export type ItemRef = { collId: string; href: string; data?: StacDoc };

const C = {
  wrap: "w-full px-3 py-4 mx-auto max-w-[1400px] sm:px-5",
  crumb: "text-primary cursor-pointer",
  muted: "text-xs text-muted-foreground",
  grid: "mt-3.5 grid gap-3 grid-cols-1 sm:grid-cols-2 lg:grid-cols-3",
  card: "rounded-lg border border-border bg-card px-3.5 py-3 cursor-pointer hover:border-primary hover:shadow-sm transition",
  cardTitle: "mb-1.5 text-sm font-semibold leading-tight",
  badge: "mr-1.5 mt-1 inline-block rounded border border-border bg-muted px-1.5 py-px text-[11px] text-muted-foreground",
  chip: "mr-1.5 mt-1.5 inline-block rounded bg-primary px-2 py-0.5 text-[11px] text-primary-foreground no-underline hover:opacity-90",
  input: "w-full sm:w-72 rounded-md border border-input bg-card px-2.5 py-1.5 text-sm text-foreground placeholder:text-muted-foreground",
  bar: "my-2 flex flex-wrap items-center gap-2.5",
  th: "cursor-pointer whitespace-nowrap border-b border-border px-2.5 py-1.5 text-left text-[11px] uppercase tracking-wide text-muted-foreground",
  thPlain: "whitespace-nowrap border-b border-border px-2.5 py-1.5 text-left text-[11px] uppercase tracking-wide text-muted-foreground",
  td: "border-b border-border px-2.5 py-1.5 align-top text-sm",
};
const toggle = (on: boolean) =>
  `cursor-pointer border border-border px-2.5 py-1 text-xs text-foreground ${on ? "bg-accent" : "bg-card"}`;

const BADGE_KEYS = ["ugs:series", "ugs:pub_type", "ugs:topic", "ugs:scale", "ugs:author"];


const idFromHref = (href: string) => href.split("/").slice(-2)[0];
const props = (it: ItemRef) => it.data?.properties ?? {};
// Survey Notes volume (warehouse ugs:volume, from SNT-{vol}-{issue}) → group issues under it.
const gVol = (it: ItemRef): number | null => {
  const v = props(it)["ugs:volume"];
  return typeof v === "number" ? v : null;
};
// The STAC item id IS the publication series id (DS-8, OFR-647, …) / the layer stem.
const gSeries = (it: ItemRef) => String(it.data?.id ?? idFromHref(it.href));
// collId is the unique collection key (e.g. `ugs-publications/B`); show just the leaf folder as label.
const gColl = (it: ItemRef) => it.collId.split("/").pop() ?? it.collId;
const gTitle = (it: ItemRef) => String(props(it).title ?? it.data?.id ?? idFromHref(it.href));
const gDate = (it: ItemRef) => (typeof props(it).datetime === "string" ? (props(it).datetime as string).slice(0, 10) : "");
const gYear = (it: ItemRef): number | null => { const y = parseInt(gDate(it).slice(0, 4), 10); return Number.isFinite(y) ? y : null; };
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
// Show the date precision we actually have. UGS pubs carry year-only (stored as a Jan-1 placeholder)
// or year+month; the day is never real — so don't render a misleading "2026-01-01". Sorting still uses
// the raw ISO from gDate; this is display-only.
const fmtDate = (iso: string): string => {
  if (!iso) return "";
  const [y, m, d] = iso.split("-");
  if (m === "01" && d === "01") return y;                  // year-only placeholder → just the year
  if (d === "01") return `${MONTHS[+m - 1] ?? m} ${y}`;    // month precision → "Sep 2026"
  return iso;                                              // genuine full date
};
const gType = (it: ItemRef) => String(props(it)["ugs:pub_type"] ?? props(it)["ugs:series"] ?? props(it)["ugs:topic"] ?? "");
const gScale = (it: ItemRef) => String(props(it)["ugs:scale"] ?? "");
const gAuthor = (it: ItemRef) => String(props(it)["ugs:author"] ?? "");
const gCounty = (it: ItemRef) => String(props(it)["ugs:county"] ?? "");
// Bin a free-text publication scale into a tier (matches the raster-mosaic tiers). "" = unknown.
const scaleTierOf = (it: ItemRef): string => {
  const s = gScale(it).replace(/,/g, "").toLowerCase();
  let m = s.match(/1\s*:\s*(\d+)/);
  let d = m ? +m[1] : NaN;
  if (!Number.isFinite(d)) { m = s.match(/1\s*in(?:ch)?\s*=\s*([\d.]+)\s*feet/); if (m) d = +m[1] * 12; }
  if (!Number.isFinite(d)) { m = s.match(/1\s*in(?:ch)?\s*=\s*([\d.]+)\s*mile/); if (m) d = +m[1] * 63360; }
  if (!Number.isFinite(d)) return "";
  return d <= 62500 ? "24k" : d <= 350000 ? "250k" : "500k";
};
const haystack = (it: ItemRef) => (it.href + JSON.stringify(it.data?.properties ?? {})).toLowerCase();
// "Mappable" = has something to draw on the map: a COG (raster), vector PMTiles, or a raster PMTiles
// mosaic. Items with none (metadata-only pubs) do nothing when toggled — the filter hides them.
const hasMapData = (it: ItemRef) => !!(cogAsset(it.data) || pmtilesLink(it.data) || rasterTilesAsset(it.data));
// Item carries an interactive 3D fence-diagram asset (role 3d-vector) → eligible for the 3D viewer.
const has3D = (it: ItemRef) => Object.values(it.data?.assets ?? {}).some((a) => assetKind(a as Asset) === "threeD");
// Data-series code = the alpha prefix of the publication series id (DS-8 → DS, OFR-647 →
// OFR). Only items that carry `ugs:series_id` (publications) get a code; everything else
// (vector serving topics, etc.) returns "" so it never pollutes the series facet. Numeric
// or prefixless pub ids bucket as "Other". gLabel is the human name for the chip tooltip.
const gCode = (it: ItemRef) => {
  const sid = props(it)["ugs:series_id"];
  if (typeof sid !== "string" || !sid) return "";
  const m = sid.match(/^[A-Za-z]+/);
  return m ? m[0].toUpperCase() : "Other";
};
const gLabel = (it: ItemRef) => gType(it) || gCode(it);

// Group the warehouse's fine ugs:topic into the 4 public map-pub topics (+ Other = sectioned off).
const TOPIC_GROUP: Record<string, string> = {
  hazards: "Hazards", "mineral-energy": "Energy & Minerals", hydro: "Groundwater & Wetlands",
  geologic: "Geologic Map", surficial: "Geologic Map", geophysics: "Geologic Map",
};
const gTopic = (it: ItemRef) => TOPIC_GROUP[String(props(it)["ugs:topic"] ?? "")] ?? "Other";

// Sorted [key, {n, label}] facet counts. `extract` returns "" to skip an item; `label` (optional)
// is the human name for a chip tooltip. Shared by the series + topic facets.
function buildFacets(items: ItemRef[], extract: (it: ItemRef) => string, label?: (it: ItemRef) => string) {
  const m = new Map<string, { n: number; label: string }>();
  for (const it of items) {
    const k = extract(it);
    if (!k) continue;
    const cur = m.get(k) ?? { n: 0, label: label?.(it) ?? "" };
    m.set(k, { n: cur.n + 1, label: cur.label });
  }
  return [...m.entries()].sort((a, b) => b[1].n - a[1].n || a[0].localeCompare(b[0]));
}


function AssetChips({ assets }: { assets: Record<string, Asset> }) {
  return (
    <>
      {Object.entries(assets).map(([k, a]) => (
        <a key={k} className={C.chip} href={a.href} target="_blank" rel="noopener"
          onClick={(e) => e.stopPropagation()}>{a.title ?? k}</a>
      ))}
    </>
  );
}

const parquetAsset = (item: StacDoc): Asset | undefined =>
  Object.values(item.assets ?? {}).find(
    (a) => a.type?.includes("parquet") || a.href.endsWith(".parquet"),
  );

// Client-side export — only for items with a GeoParquet asset (serving topics).
function ExportPanel({ item }: { item: StacDoc }) {
  const parquet = parquetAsset(item);
  const fullBbox = item.bbox?.slice(0, 4) as [number, number, number, number] | undefined;
  const [busy, setBusy] = useState<ExportFormat | null>(null);
  const [err, setErr] = useState<string>();
  const [clipOn, setClipOn] = useState(false);
  const [bbox, setBbox] = useState<[number, number, number, number]>(fullBbox ?? [0, 0, 0, 0]);
  const [warn, setWarn] = useState<ShapefileWarnings | null>(null);  // shapefile pre-flight issues
  const [epsg, setEpsg] = useState(4326);                            // output CRS for the gdal formats
  const [customEpsg, setCustomEpsg] = useState(false);               // typed any-EPSG vs the common list
  if (!parquet) return null;

  const doExport = async (fmt: ExportFormat) => {
    setBusy(fmt);
    try {
      await exportItem(parquet.href, String(item.id ?? "export"), fmt, clipOn ? bbox : undefined, epsg);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const run = async (fmt: ExportFormat) => {
    setErr(undefined);
    setWarn(null);
    // Shapefile pre-flight: warn before handing over a silently-mangled file.
    if (fmt === "shp") {
      setBusy(fmt);
      try {
        const w = await shapefileWarnings(parquet.href, clipOn ? bbox : undefined);
        if (w.any) { setWarn(w); setBusy(null); return; }
      } catch { /* check failed → just proceed to the export */ }
    }
    await doExport(fmt);
  };

  const labels = ["W", "S", "E", "N"];
  return (
    <div className="mt-3 rounded-lg border border-border bg-muted p-3">
      <div className="mb-1.5 text-xs font-semibold text-muted-foreground">Download as</div>
      <div className="flex flex-wrap items-center gap-2">
        {FORMATS.map((f) => (
          <button key={f.id} disabled={busy !== null} onClick={() => run(f.id)}
            className="rounded border border-border bg-card px-2.5 py-1 text-xs text-foreground hover:border-primary disabled:opacity-50">
            {busy === f.id ? "preparing…" : f.label}
          </button>
        ))}
        {busy && <span className={C.muted}>running in your browser · first export loads DuckDB (~a few MB)</span>}
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
        <label className="flex items-center gap-1">
          Output CRS
          <select value={customEpsg ? "other" : epsg}
            onChange={(e) => {
              if (e.target.value === "other") { setCustomEpsg(true); }
              else { setCustomEpsg(false); setEpsg(Number(e.target.value)); }
            }}
            className="rounded border border-input bg-card px-1.5 py-0.5 text-foreground">
            <option value={4326}>WGS 84 (EPSG:4326)</option>
            <option value={26912}>NAD83 / UTM 12N (EPSG:26912)</option>
            <option value={32612}>WGS84 / UTM 12N (EPSG:32612)</option>
            <option value={3857}>Web Mercator (EPSG:3857)</option>
            <option value="other">Other (any EPSG)…</option>
          </select>
        </label>
        {customEpsg && (
          <label className="flex items-center gap-1">
            EPSG:
            <input type="number" min={1024} max={999999} value={epsg} autoFocus
              onChange={(e) => setEpsg(Number(e.target.value))}
              className="w-24 rounded border border-input bg-card px-1.5 py-0.5 text-foreground" />
          </label>
        )}
        <span>— applies to Shapefile/GeoPackage/FileGDB/FlatGeobuf and CSV; GeoJSON is always WGS 84 (spec).</span>
      </div>
      {fullBbox && (
        <div className="mt-2 text-xs">
          <label className="flex items-center gap-1.5 text-muted-foreground">
            <input type="checkbox" checked={clipOn} onChange={(e) => setClipOn(e.target.checked)} />
            Clip to area (bbox, EPSG:4326)
          </label>
          {clipOn && (
            <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
              {bbox.map((v, i) => (
                <label key={i} className="flex items-center gap-1 text-muted-foreground">
                  {labels[i]}
                  <input type="number" step="0.01" value={v}
                    onChange={(e) => setBbox((b) => b.map((x, j) => (j === i ? Number(e.target.value) : x)) as typeof b)}
                    className="w-24 rounded border border-input bg-card px-1.5 py-0.5 text-foreground" />
                </label>
              ))}
              <button onClick={() => setBbox(fullBbox)} className="text-primary">reset</button>
            </div>
          )}
        </div>
      )}
      {err && <div className="mt-1.5 text-xs text-destructive">Export failed: {err}</div>}

      {warn && (
        <div className="mt-2 rounded-md border border-amber-500/50 bg-amber-500/10 p-2.5 text-xs">
          <div className="font-semibold text-amber-700 dark:text-amber-400">Shapefile will mangle this data</div>
          <ul className="mt-1 list-disc space-y-0.5 pl-4 text-foreground">
            {warn.mixedGeometry.length > 0 && (
              <li><b>Mixed geometry</b> ({warn.mixedGeometry.join(", ").toLowerCase()}) — a shapefile holds one geometry type; the others get dropped. Use GeoPackage.</li>
            )}
            {warn.longNames.length > 0 && (
              <li><b>{warn.longNames.length} field name{warn.longNames.length === 1 ? "" : "s"} over 10 chars</b> get truncated (e.g. <code>{warn.longNames[0]}</code> → <code>{warn.longNames[0].slice(0, 10)}</code>).</li>
            )}
            {warn.collisions.length > 0 && (
              <li><b>Field-name collisions</b> after truncation — <code>{warn.collisions[0][0]}</code> &amp; <code>{warn.collisions[0][1]}</code> collapse to the same name (data loss).</li>
            )}
            {warn.tooManyFields && <li><b>{warn.fieldCount} fields</b> exceeds the 255-field shapefile limit.</li>}
            {warn.over2gb && <li><b>~{(warn.estBytes / 1024 ** 3).toFixed(1)} GB estimated</b> — over the 2 GB shapefile limit (estimate; export may fail).</li>}
          </ul>
          <div className="mt-2 flex flex-wrap gap-2">
            <button onClick={() => { setWarn(null); doExport("gpkg"); }}
              className="rounded border border-border bg-primary px-2 py-0.5 text-primary-foreground hover:opacity-90">
              Use GeoPackage instead
            </button>
            <button onClick={() => { setWarn(null); doExport("shp"); }}
              className="rounded border border-border bg-card px-2 py-0.5 text-foreground hover:border-primary">
              Download shapefile anyway
            </button>
            <button onClick={() => setWarn(null)} className="text-muted-foreground hover:underline">Cancel</button>
          </div>
        </div>
      )}
    </div>
  );
}

// ---- collection cards ----
const humanize = (id: string) =>
  id.replace(/^ugs-/, "").replace(/[-_]+/g, " ").replace(/\b\w/g, (ch) => ch.toUpperCase());
// The warehouse emits a placeholder "UGS warehouse — {id}." description; hide it as noise.
const meaningfulDesc = (d?: string) => (d && !/^UGS warehouse — .*\.$/.test(d) ? d : null);

function Collections({ collections, heading, onOpen, onOpenItem }: {
  collections: CollectionSummary[]; heading: string; onOpen: (href: string) => void;
  onOpenItem: (href: string) => void;
}) {
  if (!collections.length) return <p className={`${C.muted} mt-4`}>Nothing here yet.</p>;
  return (
    <>
      <h2 className="mb-1 mt-1 text-lg font-semibold">{heading}</h2>
      <div className={C.grid}>
        {collections.map((c) => {
          const desc = meaningfulDesc(c.description);
          return (
            <div key={c.href} className={C.card} onClick={() => onOpen(c.href)}>
              <p className="text-base font-semibold leading-tight">{c.title ?? humanize(c.id)}</p>
              <div className="mt-0.5 font-mono text-[11px] text-muted-foreground">{c.id}</div>
              {desc && <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">{desc}</p>}
              {c.covers && c.covers.length > 0 && (
                <div className="mt-2 flex gap-1 overflow-hidden" title="Latest covers — click to open">
                  {c.covers.map((cv) => (
                    <img key={cv.href} src={cv.thumb} alt={cv.title ?? ""} loading="lazy" title={cv.title ?? ""}
                      onClick={(e) => { e.stopPropagation(); onOpenItem(cv.href); }}
                      className="h-16 w-12 shrink-0 cursor-pointer rounded-sm border border-border bg-muted object-cover hover:border-primary" />
                  ))}
                </div>
              )}
              {c.count != null && <span className={`${C.badge} mt-2`}>{c.count} item{c.count === 1 ? "" : "s"}</span>}
              {c.kind === "catalog" && <span className={`${C.badge} mt-2`}>by series</span>}
              {c.mappable === 0
                ? <span className="mt-2 ml-1 rounded bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground">no map data</span>
                : c.mappable != null && c.count != null && c.mappable < c.count
                  ? <span className="mt-2 ml-1 rounded bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground">{c.mappable} on map</span>
                  : null}
            </div>
          );
        })}
      </div>
    </>
  );
}

function Breadcrumb({ crumbs }: { crumbs: { label: string; onClick?: () => void }[] }) {
  return (
    <div className="mb-1 text-sm">
      {crumbs.map((c, i) => (
        <span key={i}>
          {i > 0 && <span className={C.muted}> / </span>}
          {c.onClick ? <span className={C.crumb} onClick={c.onClick}>{c.label}</span>
            : <span className={i === crumbs.length - 1 ? "" : C.muted}>{c.label}</span>}
        </span>
      ))}
    </div>
  );
}

// Thumbnail (cover) grid. When items carry a Survey Notes volume (ugs:volume), they're grouped
// under "Volume N" headers, newest volume first; otherwise a single flat grid.
const THUMB_GRID = "grid grid-cols-1 gap-3 sm:grid-cols-2 md:grid-cols-4 lg:grid-cols-6";
function ThumbCard({ it, onOpen }: { it: ItemRef; onOpen: (href: string) => void }) {
  const th = thumbnailAsset(it.data);
  return (
    <div onClick={() => onOpen(it.href)}
      className="cursor-pointer overflow-hidden rounded-md border border-border bg-card hover:border-primary">
      <div className="flex aspect-[3/4] items-center justify-center overflow-hidden bg-muted">
        {th ? <img src={th.href} alt={gTitle(it)} loading="lazy" className="h-full w-full object-cover" />
            : <span className="p-2 text-center font-mono text-xs text-muted-foreground">{gSeries(it)}</span>}
      </div>
      <div className="p-1.5">
        <div className="font-mono text-[11px] font-semibold text-foreground">{gSeries(it)}</div>
        <p className="line-clamp-2 text-[11px] text-muted-foreground">{gTitle(it)}</p>
      </div>
    </div>
  );
}
// A card in the Cards view (text-forward; same data as the table row).
function CardItem({ it, showCollection, onOpen }: { it: ItemRef; showCollection?: boolean; onOpen: (href: string) => void }) {
  return (
    <div className={C.card} onClick={() => onOpen(it.href)}>
      <div className="font-mono text-[12px] font-semibold text-foreground">{gSeries(it)}</div>
      <p className={C.cardTitle}>{gTitle(it)}</p>
      <div>
        {showCollection && <span className={C.badge}>{gColl(it)}</span>}
        {gDate(it) && <span className={C.badge}>{fmtDate(gDate(it))}</span>}
        {BADGE_KEYS.filter((k) => props(it)[k]).map((k) => (
          <span key={k} className={C.badge}>{String(props(it)[k])}</span>
        ))}
      </div>
      {it.data?.assets && <div><AssetChips assets={it.data.assets} /></div>}
    </div>
  );
}

// Wrap any grid renderer with Survey Notes "Volume N" section headers (newest volume first) when the
// items carry ugs:volume — otherwise a single flat grid. Shared by the Thumbnails + Cards views so
// volume grouping is consistent, not view-specific.
function VolumeGrouped({ rows, gridClass, render }: {
  rows: ItemRef[]; gridClass: string; render: (it: ItemRef) => React.ReactNode;
}) {
  if (!rows.some((it) => gVol(it) != null))
    return <div className={gridClass}>{rows.map(render)}</div>;
  const groups = new Map<number, ItemRef[]>();
  const other: ItemRef[] = [];
  for (const it of rows) {
    const v = gVol(it);
    if (v == null) other.push(it);
    else (groups.get(v) ?? groups.set(v, []).get(v)!).push(it);
  }
  const sections: [string, ItemRef[]][] = [...groups.keys()].sort((a, b) => b - a).map((v) => [`Volume ${v}`, groups.get(v)!]);
  if (other.length) sections.push(["Other", other]);
  return (
    <div className="space-y-4">
      {sections.map(([label, items]) => (
        <div key={label}>
          <h3 className="mb-1.5 text-sm font-semibold text-muted-foreground">{label}</h3>
          <div className={gridClass}>{items.map(render)}</div>
        </div>
      ))}
    </div>
  );
}

// One review comment applied to N selected items at once (item_ids array). Review deploy only.
function BulkItemComposer({ itemIds, onDone }: { itemIds: string[]; onDone: () => void }) {
  const qc = useQueryClient();
  const [body, setBody] = useState("");
  const add = useMutation({
    mutationFn: () => createComment(itemIds, body, { kind: "item" }),
    onSuccess: () => { setBody(""); qc.invalidateQueries({ queryKey: ["comments-all"] }); itemIds.forEach((id) => qc.invalidateQueries({ queryKey: ["comments", id] })); onDone(); },
  });
  return (
    <div className="mb-2 rounded-md border border-amber-500/40 bg-amber-500/[0.04] p-2 text-xs">
      <div className="mb-1 font-medium">New comment on {itemIds.length} item{itemIds.length === 1 ? "" : "s"}</div>
      <div className="flex gap-1.5">
        <textarea value={body} onChange={(e) => setBody(e.target.value)} rows={2}
          placeholder="Add a review note for the selected items…"
          className="flex-1 rounded border border-border bg-background px-2 py-1 text-xs" />
        <button disabled={!body.trim() || add.isPending} onClick={() => add.mutate()}
          className="self-end rounded border border-border bg-primary px-2 py-1 text-primary-foreground hover:opacity-90 disabled:opacity-60">
          {add.isPending ? "…" : "Add"}
        </button>
      </div>
      {add.error && <p className="mt-1 text-destructive">Failed: {String(add.error)}</p>}
    </div>
  );
}

// ---- item list: filter + sort + table/cards, reused for a collection and global search ----
function ItemList({ items, showCollection, query, onOpen, series, onSeries, force3D }: {
  items: ItemRef[]; showCollection?: boolean; query?: string; onOpen: (href: string) => void;
  series: string[]; onSeries: (codes: string[]) => void; force3D?: boolean;
}) {
  const [q, setQ] = useState("");
  const [mode, setMode] = useState<"table" | "cards" | "thumbs">("table");
  // Review deploy: bulk-select items → one comment on all of them (backend item_ids is an array).
  const [selItems, setSelItems] = useState<Set<string>>(new Set());
  const [itemComposeOpen, setItemComposeOpen] = useState(false);
  const toggleItem = (id: string) => setSelItems((prev) => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });
  const [mapOnly, setMapOnly] = useState(false);  // hide metadata-only items (no COG / no tiles)
  const [yearMin, setYearMin] = useState("");
  const [yearMax, setYearMax] = useState("");
  const [topics, setTopics] = useState<string[]>([]);  // map-pub topic filter (multi-select)
  const [author, setAuthor] = useState("");            // author substring filter (→ all pubs by X)
  const [scaleTier, setScaleTier] = useState<string | null>(null);  // 24k / 250k / 500k
  const [counties, setCounties] = useState<string[]>([]);  // county filter (multi-select)

  // Data-series facets — one chip per series code (DS, OFR, GQ…), with a count and the
  // human label. Multi-select: pick any combination; the selection lives in the URL
  // (?s=DS,OFR) so a "just the series I care about" view is shareable. Counts derive from
  // the full set so they stay stable as you toggle.
  const facets = useMemo(() => buildFacets(items, gCode, gLabel), [items]);
  const sel = new Set(series);
  const toggleCode = (code: string) =>
    onSeries(sel.has(code) ? series.filter((c) => c !== code) : [...series, code]);

  // Topic facets (the 4 map-pub topics + Other), only over items that carry ugs:topic.
  const topicFacets = useMemo(
    () => buildFacets(items, (it) => (props(it)["ugs:topic"] ? gTopic(it) : "")), [items]);
  const tsel = new Set(topics);
  const toggleTopic = (t: string) => setTopics(tsel.has(t) ? topics.filter((x) => x !== t) : [...topics, t]);

  // County facets (derived from lat/lon, so only over items that carry one).
  const countyFacets = useMemo(() => buildFacets(items, gCounty), [items]);
  const csel = new Set(counties);
  const toggleCounty = (c: string) => setCounties(csel.has(c) ? counties.filter((x) => x !== c) : [...counties, c]);

  // Scale-tier facets (24k/250k/500k) + the distinct author names for autocomplete.
  const scaleFacets = useMemo(() => buildFacets(items, scaleTierOf), [items]);
  const authorList = useMemo(() => {
    const s = new Set<string>();
    for (const it of items) { const a = gAuthor(it); if (a) s.add(a); }
    return [...s].sort();
  }, [items]);
  const authorNeedle = author.trim().toLowerCase();

  const needle = (query ?? q).trim().toLowerCase();
  const ymin = parseInt(yearMin, 10);
  const ymax = parseInt(yearMax, 10);
  const rows = useMemo(
    () => {
      const filtered = items.filter((it) => {
        if (needle && !haystack(it).includes(needle)) return false;
        if (sel.size && !sel.has(gCode(it))) return false;
        if (tsel.size && !tsel.has(gTopic(it))) return false;
        if (authorNeedle && !gAuthor(it).toLowerCase().includes(authorNeedle)) return false;
        if (scaleTier && scaleTierOf(it) !== scaleTier) return false;
        if (csel.size && !csel.has(gCounty(it))) return false;
        if (mapOnly && !hasMapData(it)) return false;
        if (force3D && !has3D(it)) return false;
        if (Number.isFinite(ymin) || Number.isFinite(ymax)) {
          const y = gYear(it);
          if (y == null || (Number.isFinite(ymin) && y < ymin) || (Number.isFinite(ymax) && y > ymax)) return false;
        }
        return true;
      });
      // Pubs default to newest→oldest in the thumbs/cards modes too (the table sorts itself). No-date
      // collections (vector topics) keep catalog order.
      if (filtered.some((it) => gYear(it) != null)) {
        filtered.sort((a, b) => gDate(b).localeCompare(gDate(a)) || gSeries(a).localeCompare(gSeries(b)));
      }
      return filtered;
    },
    [items, needle, series, topics, author, scaleTier, counties, mapOnly, force3D, yearMin, yearMax],
  );

  const hasVolumes = useMemo(() => items.some((it) => gVol(it) != null), [items]);
  // Review deploy: a leading checkbox column to bulk-select items for one shared comment.
  const selectColumn: ColumnDef<ItemRef, unknown> = {
    id: "sel", header: () => "", enableSorting: false, accessorFn: () => "",
    cell: ({ row }) => {
      const id = itemIdOf(row.original);
      return <input type="checkbox" aria-label={`Select ${id}`} checked={selItems.has(id)}
        onClick={(e) => e.stopPropagation()} onChange={() => toggleItem(id)} />;
    },
  };
  const columns = useMemo<ColumnDef<ItemRef, unknown>[]>(() => [
    ...(IS_REVIEW ? [selectColumn] : []),
    { id: "id", header: "ID", accessorFn: gSeries, sortingFn: "alphanumeric",
      cell: (i) => <span className="whitespace-nowrap font-mono text-[13px] font-semibold text-foreground">{String(i.getValue())}</span> },
    { id: "title", header: "Title", accessorFn: gTitle,
      cell: (i) => <span className="text-primary">{String(i.getValue())}</span> },
    ...(showCollection ? [{ id: "collection", header: "Collection", accessorFn: gColl }] : []),
    // Volume column only when items carry one (Survey Notes) — consistent with the grouped grid views.
    ...(hasVolumes ? [{ id: "volume", header: "Vol", accessorFn: (it: ItemRef) => gVol(it) ?? "" }] : []),
    { id: "type", header: "Type", accessorFn: gType },
    { id: "date", header: "Date", accessorFn: gDate, cell: (i) => fmtDate(String(i.getValue())) },
    { id: "scale", header: "Scale", accessorFn: gScale, enableSorting: false },
    { id: "assets", header: "Assets", enableSorting: false, accessorFn: () => "",
      cell: ({ row }) => row.original.data?.assets ? <AssetChips assets={row.original.data.assets} /> : "" },
  ], [showCollection, hasVolumes, selItems]);

  // Default sort: publications (which carry a real publication year) lead newest→oldest; vector
  // serving topics (ingest-time datetime only — not meaningful) stay alphabetical by id.
  const defaultSorting = useMemo<SortingState>(
    () => (items.some((it) => gYear(it) != null)
      ? [{ id: "date", desc: true }]
      : [{ id: "id", desc: false }]),
    [items],
  );

  return (
    <>
      <div className={C.bar}>
        {query === undefined && (
          <input className={C.input} placeholder="Filter…" value={q} onChange={(e) => setQ(e.target.value)} />
        )}
        <span className={C.muted}>{rows.length} of {items.length}</span>
        <span className="flex-1" />
        {authorList.length > 0 && (
          <span className="flex items-center gap-1 text-xs text-muted-foreground" title="Filter by author">
            <input list="bx-authors" placeholder="Author…" value={author}
              onChange={(e) => setAuthor(e.target.value)}
              className="w-36 rounded border border-border bg-background px-1.5 py-0.5" />
            <datalist id="bx-authors">{authorList.map((a) => <option key={a} value={a} />)}</datalist>
          </span>
        )}
        <span className="flex items-center gap-1 text-xs text-muted-foreground" title="Filter by publication year">
          <span>Year</span>
          <input type="number" inputMode="numeric" placeholder="from" value={yearMin}
            onChange={(e) => setYearMin(e.target.value)}
            className="w-14 rounded border border-border bg-background px-1 py-0.5" />
          <span>–</span>
          <input type="number" inputMode="numeric" placeholder="to" value={yearMax}
            onChange={(e) => setYearMax(e.target.value)}
            className="w-14 rounded border border-border bg-background px-1 py-0.5" />
        </span>
        <span className={toggle(mapOnly)} title="Only items with a COG or vector tiles to display on the map"
          onClick={() => setMapOnly((v) => !v)}>Mappable</span>
        <span className={toggle(mode === "table")} onClick={() => setMode("table")}>Table</span>
        <span className={toggle(mode === "thumbs")} onClick={() => setMode("thumbs")}>Thumbnails</span>
        <span className={toggle(mode === "cards")} onClick={() => setMode("cards")}>Cards</span>
      </div>

      {topicFacets.length > 1 && (
        <div className="mb-2 flex flex-wrap items-center gap-1.5">
          <span className="mr-0.5 text-[11px] uppercase tracking-wide text-muted-foreground">Topic</span>
          {topicFacets.map(([t, { n }]) => (
            <span key={t} className={toggle(tsel.has(t))} onClick={() => toggleTopic(t)}>{t} · {n}</span>
          ))}
          {tsel.size > 0 && (
            <span className="cursor-pointer text-xs text-primary" onClick={() => setTopics([])}>clear</span>
          )}
        </div>
      )}

      {countyFacets.length > 1 && (
        <div className="mb-2 flex flex-wrap items-center gap-1.5">
          <span className="mr-0.5 text-[11px] uppercase tracking-wide text-muted-foreground">County</span>
          {countyFacets.map(([c, { n }]) => (
            <span key={c} className={toggle(csel.has(c))} onClick={() => toggleCounty(c)}>{c} · {n}</span>
          ))}
          {csel.size > 0 && (
            <span className="cursor-pointer text-xs text-primary" onClick={() => setCounties([])}>clear</span>
          )}
        </div>
      )}

      {scaleFacets.length > 1 && (
        <div className="mb-2 flex flex-wrap items-center gap-1.5">
          <span className="mr-0.5 text-[11px] uppercase tracking-wide text-muted-foreground">Scale</span>
          {scaleFacets.map(([t, { n }]) => (
            <span key={t} className={toggle(scaleTier === t)}
              onClick={() => setScaleTier(scaleTier === t ? null : t)}>{t} · {n}</span>
          ))}
          {scaleTier && (
            <span className="cursor-pointer text-xs text-primary" onClick={() => setScaleTier(null)}>clear</span>
          )}
        </div>
      )}

      {facets.length > 1 && (
        <div className="mb-2 flex flex-wrap items-center gap-1.5">
          <span className="mr-0.5 text-[11px] uppercase tracking-wide text-muted-foreground">Series</span>
          {facets.map(([code, { n, label }]) => (
            <span key={code} className={toggle(sel.has(code))} title={label}
              onClick={() => toggleCode(code)}>{code} · {n}</span>
          ))}
          {sel.size > 0 && (
            <span className="cursor-pointer text-xs text-primary" onClick={() => onSeries([])}>clear</span>
          )}
        </div>
      )}

      {rows.length === 0 ? (
        <p className={`${C.muted} mt-3`}>{needle ? "No items match." : "No items."}</p>
      ) : mode === "table" ? (
        <>
          {IS_REVIEW && selItems.size > 0 && (
            <div className="mb-1.5 flex items-center gap-2 rounded-md border border-amber-500/40 bg-amber-500/10 px-2 py-1 text-xs">
              <span className="font-medium text-amber-700 dark:text-amber-400">{selItems.size} item{selItems.size === 1 ? "" : "s"} selected</span>
              <button className="rounded border border-amber-500/50 bg-amber-500/15 px-2 py-0.5 font-medium text-amber-700 hover:bg-amber-500/25 dark:text-amber-300"
                onClick={() => setItemComposeOpen(true)}>💬 Comment on {selItems.size === 1 ? "item" : `${selItems.size} items`}</button>
              <button className="text-muted-foreground hover:underline" onClick={() => { setSelItems(new Set()); setItemComposeOpen(false); }}>clear</button>
            </div>
          )}
          {IS_REVIEW && itemComposeOpen && selItems.size > 0 && (
            <BulkItemComposer itemIds={[...selItems]} onDone={() => setItemComposeOpen(false)} />
          )}
          <div className="overflow-x-auto">
            <DataTable columns={columns} data={rows} onRowClick={(it) => onOpen(it.href)}
              initialSorting={defaultSorting} />
          </div>
        </>
      ) : mode === "thumbs" ? (
        <VolumeGrouped rows={rows} gridClass={THUMB_GRID}
          render={(it) => <ThumbCard key={it.href} it={it} onOpen={onOpen} />} />
      ) : (
        <VolumeGrouped rows={rows} gridClass={C.grid}
          render={(it) => <CardItem key={it.href} it={it} showCollection={showCollection} onOpen={onOpen} />} />
      )}
    </>
  );
}

// Schema panel from the STAC Table extension (`table:columns`). The dataset's fields + types
// straight from the catalog — no parquet read. Hidden pre-reingest (extension not emitted yet).
function FieldsPanel({ item }: { item: StacDoc }) {
  const cols = tableColumns(item);
  if (!cols) return null;
  return (
    <details className="mt-2 max-w-[1100px] rounded-md border border-border bg-card text-[12px]">
      <summary className="cursor-pointer px-3 py-2 font-semibold text-muted-foreground">
        Fields <span className="font-normal text-muted-foreground">· {cols.length}</span>
      </summary>
      <div className="flex flex-wrap gap-x-5 gap-y-1.5 px-3 pb-3">
        {cols.map((c) => (
          <span key={c.name} className="inline-flex items-baseline gap-1.5">
            <span className="font-mono text-foreground">{c.name}</span>
            {c.type && <span className="text-[11px] text-muted-foreground">{c.type}</span>}
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

// Vector asset preview: the item's PMTiles on the shared persistent map + full dataset explorer,
// linked — click a table row → map flies to that feature; click a map feature → table pages to it.
// The map instance lives in PreviewMapProvider (mounted once); this publishes the vector spec and
// wires the table↔map state (focus/pick) through the provider.
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
      <FieldsPanel item={item} />
      {pq && <DataExplorer href={pq.href} onPick={setFocus} mapPick={pick} reviewItemId={String(item.id ?? "")} rowKey={primaryKeyOf(item)} />}
    </>
  );
}

// Canonical datalake glance — first rows of the GeoParquet via DuckDB-WASM (geometry dropped).
// Reusable sortable table (TanStack Table). Headers toggle sort; pass `onRowClick` for clickable rows.
function DataTable<T>({ columns, data, onRowClick, initialSorting }: {
  columns: ColumnDef<T, unknown>[];
  data: T[];
  onRowClick?: (row: T) => void;
  initialSorting?: SortingState;
}) {
  const [sorting, setSorting] = useState<SortingState>(initialSorting ?? []);
  const table = useReactTable({
    data, columns, state: { sorting }, onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(), getSortedRowModel: getSortedRowModel(),
  });
  return (
    <table className="w-full border-collapse">
      <thead>
        {table.getHeaderGroups().map((hg) => (
          <tr key={hg.id}>
            {hg.headers.map((h) => {
              const s = h.column.getIsSorted();
              return (
                <th key={h.id} className={h.column.getCanSort() ? C.th : C.thPlain}
                  onClick={h.column.getToggleSortingHandler()}>
                  {flexRender(h.column.columnDef.header, h.getContext())}
                  {s === "asc" ? " ▲" : s === "desc" ? " ▼" : ""}
                </th>
              );
            })}
          </tr>
        ))}
      </thead>
      <tbody>
        {table.getRowModel().rows.map((r) => (
          <tr key={r.id} className={onRowClick ? "cursor-pointer hover:bg-muted" : undefined}
            onClick={onRowClick ? () => onRowClick(r.original) : undefined}>
            {r.getVisibleCells().map((c) => (
              <td key={c.id} className={C.td}>{flexRender(c.column.columnDef.cell, c.getContext())}</td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// Full dataset explorer — the whole GeoParquet, paged/sorted/searched in the browser via
// DuckDB-WASM (HTTP range reads; never downloads the whole file). Server-style manual paging:
// the page query carries LIMIT/OFFSET/ORDER BY/WHERE, so this scales to the 7000-row tables.
// Geometry is excluded (use Download / OGC API / the map for geometry).
const PAGE_SIZE = 25;
const PAGE_SIZES = [25, 50, 100, 250];
// "All" fetches up to this many rows in one page (the largest tables are ~7k); rows are virtualized
// so only the visible window renders. Capped so a pathological table can't OOM the tab.
const ALL_CAP = 100_000;
function DataExplorer({ href, onPick, mapPick, reviewItemId, rowKey = "pk" }: {
  href: string; onPick?: (sel: FocusSel) => void;
  mapPick?: { id: number; nonce: number } | null;
  reviewItemId?: string;  // review deploy: enables per-row + multi-select row comments
  rowKey?: string;        // the stable-key column (e.g. 'pk') a row comment is keyed on
}) {
  const review = Boolean(IS_REVIEW && reviewItemId);
  // Row comments: selected STABLE-key values (the pk column), tracked as a Set of string values — not
  // TanStack row-selection (server-paged, row ids reset each page) and not feature_id (ephemeral,
  // differs from the map viewer). A pk resolves to the same row across apps.
  const [selPks, setSelPks] = useState<Set<string>>(new Set());
  const [composeOpen, setComposeOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  useEffect(() => { setSelPks(new Set()); setComposeOpen(false); }, [href]);
  const togglePk = (pk: string) => setSelPks((prev) => {
    const next = new Set(prev);
    if (next.has(pk)) next.delete(pk); else next.add(pk);
    return next;
  });
  const [pageIndex, setPageIndex] = useState(0);
  const [pageSize, setPageSize] = useState(PAGE_SIZE);
  const [showAll, setShowAll] = useState(false);  // "All" rows in one virtualized page
  const [sorting, setSorting] = useState<SortingState>([]);
  const scrollRef = useRef<HTMLDivElement>(null);  // virtualizer scroll viewport (the resizable box)
  const [search, setSearch] = useState("");
  // feature_id of the row picked from the map (or a table click) — highlighted in the table.
  const [highlightId, setHighlightId] = useState<number | null>(null);
  // Raw per-column filter inputs (strings, as typed) → debounced into `applied` (SQL-ready).
  const [draft, setDraft] = useState<Record<string, { min?: string; max?: string; text?: string }>>({});
  const [applied, setApplied] = useState<{ search: string; filters: ColFilter[] }>({ search: "", filters: [] });
  const [page, setPage] = useState<{
    columns: string[]; types: Record<string, "number" | "text">;
    rows: Record<string, unknown>[]; total: number;
    bboxes: ([number, number, number, number] | null)[];
  } | null>(null);
  const [err, setErr] = useState<string>();
  const [loading, setLoading] = useState(true);

  // Debounce search + per-column filters into the applied query; a filter/search change resets to
  // page 1. Numeric columns → range (min/max), others → substring (kind from the loaded types).
  // `types` is read via a ref, NOT a dep: it's a fresh object on every page fetch, so depending on
  // it would re-run this (→ setPageIndex(0)) every time you advance a page — snapping back to 1.
  const typesRef = useRef(page?.types);
  typesRef.current = page?.types;
  useEffect(() => {
    const t = setTimeout(() => {
      const filters: ColFilter[] = [];
      for (const [col, d] of Object.entries(draft)) {
        const kind = typesRef.current?.[col] ?? "text";
        if (kind === "number") {
          const min = d.min?.trim() ? Number(d.min) : undefined;
          const max = d.max?.trim() ? Number(d.max) : undefined;
          if (Number.isFinite(min) || Number.isFinite(max)) filters.push({ col, kind, min, max });
        } else if (d.text?.trim()) {
          filters.push({ col, kind: "text", contains: d.text });
        }
      }
      setApplied({ search, filters });
      setPageIndex(0);
    }, 300);
    return () => clearTimeout(t);
  }, [search, draft]);

  const sort = sorting[0];
  const filterKey = JSON.stringify(applied.filters);
  useEffect(() => {
    let live = true;
    setLoading(true);
    import("./download").then(({ queryParquet }) => queryParquet(href, {
      limit: showAll ? ALL_CAP : pageSize, offset: showAll ? 0 : pageIndex * pageSize,
      orderBy: sort?.id, desc: sort?.desc, search: applied.search, filters: applied.filters,
    }))
      .then((d) => { if (live) { setPage(d); setErr(undefined); } })
      .catch((e) => { if (live) setErr(e instanceof Error ? e.message : String(e)); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [href, pageIndex, pageSize, showAll, sort?.id, sort?.desc, applied.search, filterKey]);

  const columns = useMemo<ColumnDef<Record<string, unknown>, unknown>[]>(
    () => (page?.columns ?? []).map((c) => ({
      id: c, header: c, accessorFn: (row) => row[c],
      cell: (info) => {
        const v = info.getValue();
        const s = v == null ? "" : String(v);
        return <span className="block max-w-[280px] truncate" title={s}>{s}</span>;
      },
    })),
    [page?.columns],
  );

  // Server-side sort/page: TanStack renders + drives the sort UI only (manualSorting), the SQL
  // does the work. Resetting to page 1 on a sort change keeps offset valid.
  const table = useReactTable({
    data: page?.rows ?? [], columns, state: { sorting },
    manualSorting: true, onSortingChange: (u) => { setSorting(u); setPageIndex(0); },
    getCoreRowModel: getCoreRowModel(),
  });

  const total = page?.total ?? 0;
  const pageCount = showAll ? 1 : Math.max(1, Math.ceil(total / pageSize));

  // Virtualize the rows so "All" (up to ~7k) renders only the visible window. Works for paged views
  // too (small counts → negligible overhead). Scroll viewport = the resizable box (scrollRef).
  const rowModel = table.getRowModel().rows;
  const rowVirt = useVirtualizer({
    count: rowModel.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => 29,   // single-line truncated rows; uniform enough to skip per-row measure
    overscan: 16,
  });
  const vItems = rowVirt.getVirtualItems();
  const padTop = vItems.length ? vItems[0].start : 0;
  const padBottom = vItems.length ? rowVirt.getTotalSize() - vItems[vItems.length - 1].end : 0;
  const colCount = (page?.columns?.length ?? 1) + (review ? 1 : 0);
  const btn = "rounded border border-border bg-card px-2 py-0.5 text-xs text-foreground hover:border-primary disabled:opacity-40";
  const fIn = "w-full min-w-[64px] rounded border border-input bg-card px-1 py-0.5 text-[11px] font-normal normal-case text-foreground";
  const hasFilters = Boolean(search) || applied.filters.length > 0
    || Object.values(draft).some((d) => d.min || d.max || d.text);
  const clearAll = () => { setSearch(""); setDraft({}); };

  // Row-comment selection helpers (review deploy). The pk column is the stable per-row handle.
  const pagePks = (page?.rows ?? []).map((r) => r[rowKey]).filter((v) => v != null).map(String);
  const pageAllSelected = pagePks.length > 0 && pagePks.every((p) => selPks.has(p));
  const pageSomeSelected = pagePks.some((p) => selPks.has(p));
  const selArr = [...selPks];

  // Row click → zoom + highlight. Fire the bbox immediately (instant feedback), then fetch the
  // real geometry (same filter+sort, offset = page start + row index) and upgrade the highlight.
  const pick = (i: number, bbox: [number, number, number, number]) => {
    if (!onPick) return;
    const offset = pageIndex * pageSize + i;
    const key = `row:${offset}`;          // same key for both onPick calls → one fly per click
    onPick({ bbox, key });
    import("./download").then(({ fetchGeometry }) => fetchGeometry(href,
      { orderBy: sort?.id, desc: sort?.desc, search: applied.search, filters: applied.filters }, offset))
      .then((g) => { if (g) onPick({ bbox, geometry: g, key }); })
      .catch(() => {});
  };

  // Map-feature click → highlight + fly to the real feature (looked up by id, independent of the
  // current filter) AND page the table to it under the current sort/filter. Paging is skipped if
  // the feature is filtered out of the visible set (ordinal null); the highlight + fly still fire.
  // Depends only on the click nonce, so it captures the sort/filter as of the click (re-running on
  // every filter keystroke would yank the page around).
  useEffect(() => {
    if (!mapPick) return;
    let live = true;
    setHighlightId(mapPick.id);
    const key = `map:${mapPick.nonce}`;   // unique per map click → always re-flies
    (async () => {
      const { fetchRowById, ordinalByFeatureId } = await import("./download");
      const row = await fetchRowById(href, mapPick.id);
      if (live && row && onPick) onPick({ bbox: row.bbox, geometry: row.geometry, key });
      const pos = await ordinalByFeatureId(href, mapPick.id, {
        orderBy: sort?.id, desc: sort?.desc, search: applied.search, filters: applied.filters,
      });
      if (live && pos != null) setPageIndex(Math.floor(pos / pageSize));
    })();
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mapPick?.nonce]);

  return (
    <div className="mt-2">
      <div className="mb-1.5 flex flex-wrap items-center gap-2">
        <button className="rounded border border-border px-1.5 py-0.5 text-xs text-muted-foreground hover:border-primary"
          title={collapsed ? "Expand table" : "Collapse table"} aria-expanded={!collapsed}
          onClick={() => setCollapsed((v) => !v)}>{collapsed ? "▸" : "▾"}</button>
        <input className={C.input} placeholder="Search all columns…" value={search}
          onChange={(e) => setSearch(e.target.value)} />
        <span className={C.muted}>
          {page ? `${total.toLocaleString()} row${total === 1 ? "" : "s"}` : "…"}{loading ? " · loading" : ""}
          {onPick && page?.bboxes.some(Boolean) ? " · click a row to zoom" : ""}
        </span>
        {hasFilters && <button className="text-xs text-primary" onClick={clearAll}>clear filters</button>}
      </div>
      {err && <div className="mb-1.5 text-xs text-destructive">explorer failed: {err}</div>}
      {review && selPks.size > 0 && (
        <div className="mb-1.5 flex items-center gap-2 rounded-md border border-amber-500/40 bg-amber-500/10 px-2 py-1 text-xs">
          <span className="font-medium text-amber-700 dark:text-amber-400">{selPks.size} row{selPks.size === 1 ? "" : "s"} selected</span>
          <button className="rounded border border-amber-500/50 bg-amber-500/15 px-2 py-0.5 font-medium text-amber-700 hover:bg-amber-500/25 dark:text-amber-300"
            onClick={() => setComposeOpen(true)}>💬 Comment on {selPks.size === 1 ? "row" : `${selPks.size} rows`}</button>
          <button className="text-muted-foreground hover:underline" onClick={() => { setSelPks(new Set()); setComposeOpen(false); }}>clear</button>
        </div>
      )}
      <div ref={scrollRef} className={`max-w-full resize-y overflow-auto rounded-md border border-border text-[12px] ${collapsed ? "hidden" : "h-[28rem] min-h-[10rem]"}`}>
        <table className="w-full border-collapse">
          <thead className="sticky top-0 z-10 bg-card">
            {table.getHeaderGroups().map((hg) => (
              <tr key={hg.id}>
                {review && (
                  <th className={`${C.th} w-8 text-center`} title="Select rows to comment on">
                    <input type="checkbox" aria-label="Select all rows on this page"
                      checked={pageAllSelected}
                      ref={(el) => { if (el) el.indeterminate = pageSomeSelected && !pageAllSelected; }}
                      onChange={() => setSelPks((prev) => {
                        const next = new Set(prev);
                        if (pageAllSelected) pagePks.forEach((p) => next.delete(p));
                        else pagePks.forEach((p) => next.add(p));
                        return next;
                      })} />
                  </th>
                )}
                {hg.headers.map((h) => {
                  const s = h.column.getIsSorted();
                  return (
                    <th key={h.id} className={C.th} onClick={h.column.getToggleSortingHandler()}>
                      {flexRender(h.column.columnDef.header, h.getContext())}
                      {s === "asc" ? " ▲" : s === "desc" ? " ▼" : ""}
                    </th>
                  );
                })}
              </tr>
            ))}
            {/* Per-column filter row: numeric → min/max range, text → substring. */}
            <tr>
              {review && <th className="border-b border-border" />}
              {(page?.columns ?? []).map((col) => {
                const kind = page?.types[col] ?? "text";
                const d = draft[col] ?? {};
                const set = (patch: Partial<{ min: string; max: string; text: string }>) =>
                  setDraft((prev) => ({ ...prev, [col]: { ...prev[col], ...patch } }));
                return (
                  <th key={col} className="border-b border-border px-1.5 py-1 align-top">
                    {kind === "number" ? (
                      <div className="flex gap-1">
                        <input className={fIn} placeholder="min" value={d.min ?? ""} type="number"
                          onChange={(e) => set({ min: e.target.value })} />
                        <input className={fIn} placeholder="max" value={d.max ?? ""} type="number"
                          onChange={(e) => set({ max: e.target.value })} />
                      </div>
                    ) : (
                      <input className={fIn} placeholder="contains…" value={d.text ?? ""}
                        onChange={(e) => set({ text: e.target.value })} />
                    )}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {/* Virtualized window: only the visible rows are in the DOM; spacer rows hold the scroll
                height above/below so "All" (up to ~7k rows) stays smooth. */}
            {padTop > 0 && <tr aria-hidden style={{ height: padTop }}><td colSpan={colCount} /></tr>}
            {vItems.map((vi) => {
              const r = rowModel[vi.index];
              const bbox = page?.bboxes[r.index] ?? null;
              const clickable = Boolean(onPick && bbox);
              const fid = r.original.feature_id;
              const nfid = fid != null ? Number(fid) : null;
              const pkRaw = r.original[rowKey];
              const pkStr = pkRaw != null ? String(pkRaw) : null;
              const hl = nfid != null && nfid === highlightId;
              return (
                <tr key={r.id} data-index={vi.index} ref={rowVirt.measureElement}
                  className={`${hl ? "bg-amber-100 dark:bg-amber-900/40" : ""} ${clickable ? "cursor-pointer hover:bg-muted" : ""}`.trim() || undefined}
                  title={clickable ? "Zoom to feature on map" : undefined}
                  onClick={clickable ? () => { pick(r.index, bbox!); if (nfid != null) setHighlightId(nfid); } : undefined}>
                  {review && (
                    <td className="w-8 px-1 text-center align-middle" onClick={(e) => e.stopPropagation()}>
                      {pkStr != null ? (
                        <div className="flex items-center gap-1">
                          <input type="checkbox" aria-label={`Select ${rowKey} ${pkStr}`}
                            checked={selPks.has(pkStr)} onChange={() => togglePk(pkStr)} />
                          <button title="Comment on this row" className="text-xs hover:opacity-70"
                            onClick={() => { setSelPks(new Set([pkStr])); setComposeOpen(true); }}>💬</button>
                        </div>
                      ) : null}
                    </td>
                  )}
                  {r.getVisibleCells().map((c) => (
                    <td key={c.id} className={C.td}>{flexRender(c.column.columnDef.cell, c.getContext())}</td>
                  ))}
                </tr>
              );
            })}
            {padBottom > 0 && <tr aria-hidden style={{ height: padBottom }}><td colSpan={colCount} /></tr>}
            {!loading && total === 0 && (
              <tr><td className="px-2.5 py-2 text-muted-foreground" colSpan={colCount}>No rows match.</td></tr>
            )}
          </tbody>
        </table>
      </div>
      <div className={`mt-1.5 flex flex-wrap items-center gap-1.5 text-xs ${collapsed ? "hidden" : ""}`}>
        <button className={btn} disabled={pageIndex === 0} onClick={() => setPageIndex(0)}>«</button>
        <button className={btn} disabled={pageIndex === 0} onClick={() => setPageIndex((i) => i - 1)}>‹ Prev</button>
        <span className="px-1 text-muted-foreground">Page {pageIndex + 1} of {pageCount}</span>
        <button className={btn} disabled={pageIndex + 1 >= pageCount} onClick={() => setPageIndex((i) => i + 1)}>Next ›</button>
        <button className={btn} disabled={pageIndex + 1 >= pageCount} onClick={() => setPageIndex(pageCount - 1)}>»</button>
        <label className="ml-1 flex items-center gap-1 text-muted-foreground">
          Rows
          <select className="rounded border border-border bg-card px-1 py-0.5 text-foreground"
            value={showAll ? "all" : pageSize}
            onChange={(e) => {
              setPageIndex(0);
              if (e.target.value === "all") { setShowAll(true); }
              else { setShowAll(false); setPageSize(Number(e.target.value)); }
            }}>
            {PAGE_SIZES.map((n) => <option key={n} value={n}>{n}</option>)}
            <option value="all">All</option>
          </select>
        </label>
        <label className="flex items-center gap-1 text-muted-foreground">
          Go to
          <input type="number" min={1} max={pageCount}
            className="w-16 rounded border border-border bg-card px-1 py-0.5 text-foreground"
            value={pageIndex + 1}
            onChange={(e) => {
              const n = Number(e.target.value);
              if (Number.isFinite(n)) setPageIndex(Math.min(pageCount, Math.max(1, n)) - 1);
            }} />
        </label>
        {total > 0 && (
          <span className="ml-auto text-muted-foreground">
            {showAll
              ? `1–${rowModel.length.toLocaleString()}${rowModel.length < total ? ` (capped at ${ALL_CAP.toLocaleString()})` : ""}`
              : `${(pageIndex * pageSize + 1).toLocaleString()}–${Math.min((pageIndex + 1) * pageSize, total).toLocaleString()}`}
            {" "}of {total.toLocaleString()}
          </span>
        )}
      </div>
      {review && composeOpen && selPks.size > 0 && (
        <div className="mt-2 max-w-3xl rounded-md border border-amber-500/40 bg-amber-500/[0.04] p-3">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold">
              {selArr.length > 1 ? `Comment on ${selArr.length} rows` : `${rowKey} ${selArr[0]}`}
            </h3>
            <button className="text-xs text-muted-foreground hover:underline" onClick={() => setComposeOpen(false)}>close</button>
          </div>
          <CommentsPanel itemId={reviewItemId!}
            target={{ kind: "row", rowKey, rowVals: selArr, rowVal: selArr.length === 1 ? selArr[0] : undefined }}
            label={selArr.length > 1 ? `New note on ${selArr.length} rows` : `Comments on ${rowKey} ${selArr[0]}`} />
        </div>
      )}
    </div>
  );
}

// ---- asset viewer: peruse a publication's files in-page (PDF / image / COG / parquet / text) ----
type AssetKind = "cog" | "threeD" | "pdf" | "image" | "parquet" | "text" | "other";
const KIND_RANK: Record<AssetKind, number> = { cog: 0, threeD: 1, pdf: 2, parquet: 3, image: 4, text: 5, other: 9 };
const KIND_LABEL: Record<AssetKind, string> = {
  cog: "Map", threeD: "3D", pdf: "PDF", parquet: "Data", image: "Image", text: "Text", other: "File",
};

const extOf = (href: string) => (href.split("?")[0].split(".").pop() ?? "").toLowerCase();
function assetKind(a: Asset): AssetKind {
  const t = (a.type ?? "").toLowerCase();
  const ext = extOf(a.href);
  if (a.roles?.includes("3d-vector") || ext.includes("3d") || a.href.includes("3d_polygons")) return "threeD";
  if (t.includes("profile=cloud-optimized") || a.roles?.includes("cloud-optimized") || a.href.endsWith(".cog.tif")) return "cog";
  if (t === "application/pdf" || ext === "pdf") return "pdf";
  if (t.startsWith("image/") || ["png", "jpg", "jpeg", "gif", "webp", "svg"].includes(ext)) return "image";
  if (t.includes("parquet") || ext === "parquet") return "parquet";
  if (t.startsWith("text/") || ["csv", "txt", "tsv"].includes(ext)) return "text";
  return "other";
}

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
    <pre className="mt-2 max-h-[600px] max-w-full overflow-auto rounded-md border border-border bg-muted p-3 text-[12px] leading-snug">
      {txt}{txt.length >= 20000 ? "\n… (truncated — open or download for the full file)" : ""}
    </pre>
  );
}

// ---- Interactive 3D Fence Diagram Viewer (deck.gl SolidPolygon/Path layers over maplibre 3D) ----
const GEOLOGIC_COLORS: Record<string, string> = {
  "red pine shale": "#556B2F",
  "zur": "#556B2F",
  "weber sandstone": "#EEDC82",
  "ipw": "#EEDC82",
  "gardison limestone": "#4682B4",
  "mg": "#4682B4",
  "deseret limestone": "#B0C4DE",
  "md": "#B0C4DE",
  "humbug formation": "#D2B48C",
  "mh": "#D2B48C",
  "keetley volcanics": "#BA55D3",
  "tk": "#BA55D3",
  "alluvium": "#FFFACD",
  "qal": "#FFFACD",
  "glacial till": "#DCDCDC",
  "qg": "#DCDCDC",
};

// HSL → hex so every unit color is a hex string — deck.gl needs RGB tuples (see hexToRgb), and a
// single format keeps the legend swatch and the 3D fill in sync.
function hslToHex(h: number, s: number, l: number): string {
  s /= 100; l /= 100;
  const k = (n: number) => (n + h / 30) % 12;
  const a = s * Math.min(l, 1 - l);
  const f = (n: number) => {
    const c = l - a * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1)));
    return Math.round(255 * c).toString(16).padStart(2, "0");
  };
  return `#${f(0)}${f(8)}${f(4)}`;
}

function getUnitColor(unit: string, label: string): string {
  const u = (unit ?? "").toLowerCase().trim();
  const l = (label ?? "").toLowerCase().trim();
  if (GEOLOGIC_COLORS[u]) return GEOLOGIC_COLORS[u];
  if (GEOLOGIC_COLORS[l]) return GEOLOGIC_COLORS[l];
  // Standard string hashing for stable geologic pastel color
  let hash = 0;
  const str = u || l || "unknown";
  for (let i = 0; i < str.length; i++) {
    hash = str.charCodeAt(i) + ((hash << 5) - hash);
  }
  return hslToHex(Math.abs(hash) % 360, 65, 60);
}

// "#rrggbb" → [r,g,b]; deck.gl color accessors want a numeric tuple, not a CSS string.
function hexToRgb(hex: string): [number, number, number] {
  const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex.trim());
  if (!m) return [128, 128, 128];
  return [parseInt(m[1], 16), parseInt(m[2], 16), parseInt(m[3], 16)];
}

// Parsed fence + sampled terrain are expensive (network fetch/parse; ~tens of 3DEP requests) and the
// viewer re-mounts every time the 3D tab is selected. Cache both per item.id at module scope so a
// second visit is instant instead of re-doing all the work.
type FenceData = {
  polygons: { unit: string; rings: number[][][] }[];
  lines: { kind: "fault" | "contact" | "boundary"; dashed: boolean; path: number[][] }[];
  legend: { unit: string; label: string; color: string }[];
  parquetFill: Record<string, string>;
  extent: { spanXY: number; zTop: number; zMid: number; half: [number, number]; bbox: [number, number, number, number]; center: [number, number]; scale: [number, number] };
};
// 3D fence + terrain meshes are multi-MB each; LRU-cap (see ./lru) so orbiting many 3D pubs can't
// grow the heap unbounded.
const FENCE_CACHE_CAP = 3;
const TERRAIN_CACHE_CAP = 3;
const fenceCache = new Map<string, FenceData>();
const terrainCache = new Map<string, TerrainMesh | null>();

function ThreeDViewer({ asset, item }: { asset: Asset; item: StacDoc }) {
  const itemId = item.id ?? asset.href;  // stable cache key (item.id is optional on StacDoc)
  const [loading, setLoading] = useState(() => !fenceCache.has(itemId));
  const [error, setError] = useState<string | null>(null);
  // Terrain runs after the fence draws; track it so the canvas can show a real progress indicator.
  const [terrainPending, setTerrainPending] = useState(false);

  // Fence parsed to LOCAL metres centred on the dataset. OrbitView is a Cartesian 3D camera (unlike
  // maplibre's 2.5D map camera) so it can orbit freely — including under the surface to look up at
  // the slice, which is the whole point of a fence diagram.
  const [polygons, setPolygons] = useState<{ unit: string; rings: number[][][] }[]>([]);
  const [lines, setLines] = useState<{ kind: "fault" | "contact" | "boundary"; dashed: boolean; path: number[][] }[]>([]);
  const [legend, setLegend] = useState<{ unit: string; label: string; color: string }[]>([]);
  const [extent, setExtent] = useState<{ spanXY: number; zTop: number; zMid: number; half: [number, number]; bbox: [number, number, number, number]; center: [number, number]; scale: [number, number] } | null>(null);
  const [terrainMesh, setTerrainMesh] = useState<TerrainMesh | null>(null);
  // Authored geologic colors (MapUnit → hex) from the publication's ArcGIS symbology — the real
  // cartography. Interim: a baked per-pub sidecar (the 3D pipeline will fold this into the GeoParquet).
  // Authored geologic colors for this pub (interim baked sidecar). Absent → getUnitColor fallback.
  const { data: authored = {} } = useQuery<Record<string, string>>({
    queryKey: ["3d-colors", item.id],
    queryFn: async ({ signal }) => {
      const r = await fetch(`${import.meta.env.BASE_URL}3d-colors/${item.id}.json`, { signal });
      return r.ok ? r.json() : {};
    },
    staleTime: 5 * 60_000,
  });
  // Per-unit fill carried in the GeoParquet `fill` column (cloud-native path) — authored, highest
  // precedence. Empty on the GeoJSON path.
  const [parquetFill, setParquetFill] = useState<Record<string, string>>({});

  const [vex, setVex] = useState(2.5);
  const [showUnits, setShowUnits] = useState(true);
  const [showLines, setShowLines] = useState(true);
  const [showSheet, setShowSheet] = useState(true);
  const [hovered, setHovered] = useState<string | null>(null);
  const [search, setSearch] = useState("");

  const polyUrl = asset.href;
  const lineUrl = polyUrl.replace("_3d_polygons.geojson", "_3d_lines.geojson");
  const cog = cogAsset(item);
  // Drape the geologic map sheet — the COG's PNG overview (browsers can't texture a COG directly).
  const sheetImg = cog ? cog.href.replace(/\.cog\.tif$/i, ".thumb.png") : undefined;

  useEffect(() => {
    let active = true;
    const ac = new AbortController();
    // Cache hit → restore parsed fence synchronously, skip the fetch/parse entirely.
    const hit = fenceCache.get(itemId);
    if (hit) {
      setPolygons(hit.polygons); setLines(hit.lines); setLegend(hit.legend);
      setParquetFill(hit.parquetFill); setExtent(hit.extent);
      setError(null); setLoading(false);
      return () => { active = false; };
    }
    setLoading(true);
    setError(null);

    // A fence feature, normalised across both sources: GeoJSON geometry (with Z) + flat props.
    type Feat = { geometry: { type?: string; coordinates?: unknown } | null; props: Record<string, unknown> };

    // Cloud-native first: 3D GeoParquet (duckdb-wasm, WKB-Z) — ONLY when the item carries a real
    // `.parquet` 3d-vector asset (the pipeline output). No same-origin probing: a missing file makes
    // duckdb throw, which the route error-boundary would catch and reset the URL. Else: GeoJSON.
    async function loadFence(): Promise<{ polyFeats: Feat[]; lineFeats: Feat[]; parquet: boolean }> {
      const assets = Object.values(item.assets ?? {}) as Asset[];
      const pq = (re: RegExp) => assets.find((a) => /\.parquet$/i.test(a.href) && re.test(a.href))?.href;
      const polyPq = pq(/polygon/i), linePq = pq(/line/i);
      if (polyPq) {
        try {
          const { readFeatures3D } = await import("./download");
          const [pf, lf] = await Promise.all([
            readFeatures3D(polyPq, ac.signal),
            linePq ? readFeatures3D(linePq, ac.signal).catch(() => []) : Promise.resolve([]),
          ]);
          if (pf.length) return { polyFeats: pf as Feat[], lineFeats: lf as Feat[], parquet: true };
        } catch { /* parquet read failed → GeoJSON */ }
      }
      const [pd, ld] = await Promise.all([
        fetch(polyUrl, { signal: ac.signal }).then((r) => { if (!r.ok) throw new Error("Polygons failed to load"); return r.json(); }),
        fetch(lineUrl, { signal: ac.signal }).then((r) => r.json()).catch(() => null),
      ]);
      const toFeat = (f: { geometry?: unknown; properties?: unknown }): Feat =>
        ({ geometry: (f.geometry ?? null) as Feat["geometry"], props: (f.properties ?? {}) as Record<string, unknown> });
      return {
        polyFeats: ((pd?.features ?? []) as { geometry?: unknown; properties?: unknown }[]).map(toFeat),
        lineFeats: ((ld?.features ?? []) as { geometry?: unknown; properties?: unknown }[]).map(toFeat),
        parquet: false,
      };
    }

    loadFence()
      .then(({ polyFeats, lineFeats }) => {
        if (!active) return;
        // Pass 1: collect raw [lon,lat,z] + bounds (centre needed before the local-metre projection).
        const rawPolys: { unit: string; rings: number[][][] }[] = [];
        const rawLines: { kind: "fault" | "contact" | "boundary"; dashed: boolean; path: number[][] }[] = [];
        const legendMap = new Map<string, string>();
        const pFill: Record<string, string> = {};
        let minLon = Infinity, minLat = Infinity, maxLon = -Infinity, maxLat = -Infinity;
        let minZ = Infinity, maxZ = -Infinity, count = 0;
        const scan = (lon: number, lat: number, z: number) => {
          minLon = Math.min(minLon, lon); maxLon = Math.max(maxLon, lon);
          minLat = Math.min(minLat, lat); maxLat = Math.max(maxLat, lat);
          minZ = Math.min(minZ, z); maxZ = Math.max(maxZ, z); count++;
        };
        const xyz = (pt: number[]): number[] => { const c = [pt[0], pt[1], pt[2] ?? 0]; scan(c[0], c[1], c[2]); return c; };

        for (const feat of polyFeats) {
          const p = feat.props;
          const unit = String(p.MapUnit ?? p.unit ?? "Unknown Unit");
          const label = String(p.label ?? p.Label ?? unit);
          if (typeof p.fill === "string") pFill[unit] = p.fill; // authored fill from the parquet
          const geom = feat.geometry || {};
          const multi: number[][][][] = geom.type === "MultiPolygon" ? geom.coordinates as number[][][][]
            : geom.type === "Polygon" ? [geom.coordinates as number[][][]] : [];
          for (const rings of multi) {
            const r3 = rings.map((ring) => ring.map(xyz));
            if (r3.length && r3[0].length) { rawPolys.push({ unit, rings: r3 }); legendMap.set(unit, label); }
          }
        }
        for (const feat of lineFeats) {
          const p = feat.props;
          // Authored line cartography from the GeMS Type/Symbol (parquet carries kind/dashed directly).
          const type = String(p.Type ?? "").toLowerCase();
          const sym = String(p.Symbol ?? "").toLowerCase();
          const kind: "fault" | "contact" | "boundary" = p.kind === "fault" || p.kind === "boundary" || p.kind === "contact"
            ? p.kind as "fault" | "contact" | "boundary"
            : type.includes("fault") ? "fault" : type.includes("boundary") ? "boundary" : "contact";
          const dashed = typeof p.dashed === "boolean" ? p.dashed : sym.includes("approxim");
          const geom = feat.geometry || {};
          const multi: number[][][] = geom.type === "MultiLineString" ? geom.coordinates as number[][][]
            : geom.type === "LineString" ? [geom.coordinates as number[][]] : [];
          for (const coords of multi) {
            const path = coords.map(xyz);
            if (path.length) rawLines.push({ kind, dashed, path });
          }
        }
        if (!count) throw new Error("No valid coordinates found in the 3D dataset");

        // Pass 2: lon/lat → local metres centred on the dataset (z stays absolute-elevation metres).
        const cLon = (minLon + maxLon) / 2, cLat = (minLat + maxLat) / 2;
        const mLon = 111320 * Math.cos((cLat * Math.PI) / 180), mLat = 110574;
        const toLocal = (p: number[]): number[] => [(p[0] - cLon) * mLon, (p[1] - cLat) * mLat, p[2]];

        const halfX = ((maxLon - minLon) / 2) * mLon, halfY = ((maxLat - minLat) / 2) * mLat;
        const fence: FenceData = {
          polygons: rawPolys.map((d) => ({ ...d, rings: d.rings.map((r) => r.map(toLocal)) })),
          lines: rawLines.map((l) => ({ ...l, path: l.path.map(toLocal) })),
          legend: Array.from(legendMap.entries()).map(([unit, label]) => ({ unit, label, color: "" })).sort((a, b) => a.unit.localeCompare(b.unit)),
          parquetFill: pFill,
          extent: {
            spanXY: Math.max(halfX, halfY) * 2, zTop: maxZ, zMid: (minZ + maxZ) / 2, half: [halfX, halfY],
            bbox: [minLon, minLat, maxLon, maxLat], center: [cLon, cLat], scale: [mLon, mLat],
          },
        };
        lruSet(fenceCache, String(itemId), fence, FENCE_CACHE_CAP);
        if (!active) return;
        setParquetFill(fence.parquetFill);
        setPolygons(fence.polygons);
        setLines(fence.lines);
        setLegend(fence.legend);
        setExtent(fence.extent);
        setLoading(false);
      })
      .catch((err) => { if (active && !ac.signal.aborted) { setError(err instanceof Error ? err.message : "Failed to load 3D data files"); setLoading(false); } });

    return () => { active = false; ac.abort(); };
  }, [item.id, polyUrl, lineUrl]);

  // Build the DEM terrain mesh once the dataset extent is known. Span the MAP-SHEET bbox (item.bbox),
  // not the fence bbox — the fence is only a transect (~40% of the quad), so draping the full-quad COG
  // over the fence extent would mis-size + misregister it. Built in the fence's local frame so the
  // fence sits as a transect within the full-size map; the COG textures it correctly.
  useEffect(() => {
    if (!extent) { setTerrainMesh(null); return; }
    let active = true;
    // Cache hit → reuse the sampled mesh, skip the ~tens of 3DEP requests.
    if (terrainCache.has(itemId)) {
      setTerrainMesh(terrainCache.get(itemId) ?? null);
      setTerrainPending(false);
      return () => { active = false; };
    }
    const mapBbox = (item.bbox?.slice(0, 4) as [number, number, number, number] | undefined) ?? extent.bbox;
    // Live USGS 3DEP (CORS-open, public domain, 1 m lidar over Utah) — no hosting, any pub's bbox.
    // Progressive: a coarse grid lands in ~1–2 s so the surface shows immediately, then a fine grid
    // samples in the background and swaps in (smooth — no facets, the draped sheet stops looking
    // tessellated). Cache the fine result so revisits skip both passes.
    setTerrainPending(true);
    const ac = new AbortController();
    (async () => {
      try {
        const coarse = await buildMeshFrom3DEP(mapBbox, extent.center, extent.scale, 48, ac.signal);
        if (!active) return;
        if (coarse) setTerrainMesh(coarse);
        const fine = await buildMeshFrom3DEP(mapBbox, extent.center, extent.scale, 160, ac.signal);
        if (ac.signal.aborted) return;  // don't cache a half-sampled (aborted) mesh
        lruSet(terrainCache, String(itemId), fine ?? coarse, TERRAIN_CACHE_CAP);
        if (!active) return;
        if (fine ?? coarse) setTerrainMesh(fine ?? coarse);
      } catch { if (active) setTerrainMesh(null); }
      finally { if (active) setTerrainPending(false); }
    })();
    return () => { active = false; ac.abort(); };
  }, [extent, item]);

  // Unit colour, standard-first: STAC classification:classes (the warehouse's built-in mechanism, what
  // the 3D pipeline will stamp) → interim per-pub sidecar → derived placeholder.
  const clsColors = useMemo(() => classificationColors(item), [item]);
  // Precedence: GeoParquet `fill` column → STAC classification:classes → interim sidecar → placeholder.
  const colorOf = (unit: string) => parquetFill[unit] ?? clsColors[unit] ?? authored[unit] ?? getUnitColor(unit, "");

  const layers = useMemo(() => {
    const out: unknown[] = [];
    if (showSheet && extent && terrainMesh) {
      // Terrain surface: DEM mesh, exaggerated via getScale (z only) to match the fence, draped with
      // the geologic map sheet. getColor white = show the texture as-is.
      out.push(new SimpleMeshLayer({
        id: "terrain",
        data: [{ position: [0, 0, 0] }],
        coordinateSystem: COORDINATE_SYSTEM.CARTESIAN,
        mesh: terrainMesh as never,
        texture: sheetImg,
        getPosition: () => [0, 0, 0],
        getColor: [255, 255, 255],
        getScale: [1, 1, vex],
        material: false,
        updateTriggers: { getScale: [vex] },
      }) as unknown);
    } else if (showSheet && sheetImg && extent) {
      // Fallback flat plane while the DEM mesh loads (or where there's no terrarium coverage).
      const z = extent.zTop * vex, [hx, hy] = extent.half;
      out.push(new BitmapLayer({
        id: "map-sheet",
        coordinateSystem: COORDINATE_SYSTEM.CARTESIAN,
        image: sheetImg,
        bounds: [[-hx, -hy, z], [-hx, hy, z], [hx, hy, z], [hx, -hy, z]] as never,
        opacity: 0.9,
      }) as unknown);
    }
    if (showUnits) {
      out.push(new SolidPolygonLayer({
        id: "fence-units",
        data: polygons,
        coordinateSystem: COORDINATE_SYSTEM.CARTESIAN,
        _full3d: true,
        getPolygon: ((d: { rings: number[][][] }) => d.rings.map((ring) => ring.map((p) => [p[0], p[1], p[2] * vex]))) as never,
        getFillColor: (d: { unit: string }) => {
          const [r, g, b] = hexToRgb(colorOf(d.unit));
          const a = hovered ? (d.unit === hovered ? 240 : 55) : 200;
          return [r, g, b, a];
        },
        pickable: true,
        onHover: (info: { object?: { unit: string } }) => setHovered(info?.object?.unit ?? null),
        updateTriggers: { getPolygon: [vex], getFillColor: [hovered, authored, clsColors, parquetFill] },
      }) as unknown);
    }
    if (showLines) {
      out.push(new PathLayer({
        id: "fence-lines",
        data: lines,
        coordinateSystem: COORDINATE_SYSTEM.CARTESIAN,
        getPath: ((d: { path: number[][] }) => d.path.map((p) => [p[0], p[1], p[2] * vex])) as never,
        // Authored: all black; faults heavier than contacts, section boundary thin.
        getColor: [25, 25, 25],
        getWidth: (d: { kind: string }) => (d.kind === "fault" ? 2.6 : d.kind === "boundary" ? 0.8 : 1.3),
        // Dashed = "approximately located" (geologic convention); solid = well located.
        getDashArray: (d: { dashed: boolean }) => (d.dashed ? [5, 3] : [0, 0]),
        dashJustified: true,
        extensions: [new PathStyleExtension({ dash: true })],
        widthUnits: "pixels",
        widthMinPixels: 1,
        updateTriggers: { getPath: [vex] },
      }) as unknown);
    }
    return out;
  }, [polygons, lines, showUnits, showLines, showSheet, sheetImg, extent, terrainMesh, vex, hovered, authored, clsColors, parquetFill]);

  if (loading) return <div className="mt-2 text-sm text-muted-foreground p-8 text-center bg-muted/20 border border-border rounded-lg">Loading 3D subsurface geometries…</div>;
  if (error) return <div className="mt-2 text-sm text-destructive p-4 bg-destructive/10 border border-destructive/20 rounded-lg">Failed to render 3D Fence Diagram: {error}</div>;

  // Frame the full map sheet (so the fence reads as a transect within it), centred on the map — not
  // the fence — since the fence sits off-centre in the quad.
  const mb = item.bbox?.slice(0, 4) as [number, number, number, number] | undefined;
  const mapCtr: [number, number] = mb && extent
    ? [((mb[0] + mb[2]) / 2 - extent.center[0]) * extent.scale[0], ((mb[1] + mb[3]) / 2 - extent.center[1]) * extent.scale[1]]
    : [0, 0];
  const mapSpan = mb && extent ? Math.max((mb[2] - mb[0]) * extent.scale[0], (mb[3] - mb[1]) * extent.scale[1]) : extent?.spanXY ?? 1;
  const initialViewState = {
    target: [mapCtr[0], mapCtr[1], (extent?.zMid ?? 0) * vex] as [number, number, number],
    rotationX: 25,    // pitch above the horizon
    rotationOrbit: -25, // azimuth
    zoom: Math.log2(520 / Math.max(mapSpan, 1)),
    minZoom: -12, maxZoom: 40,
  };

  return (
    <div className="mt-2 flex flex-col md:flex-row gap-4 border border-border rounded-lg bg-card overflow-hidden h-[640px]">
      {/* Free 3D orbit scene (deck.gl OrbitView) — Cartesian, flies under the surface. */}
      <div className="flex-1 relative bg-[#0F172A] h-[400px] md:h-full">
        <DeckGL
          views={new OrbitView({ orbitAxis: "Z" })}
          initialViewState={initialViewState}
          controller={true}
          layers={layers as never}
          getCursor={() => "grab"}
          style={{ position: "relative", width: "100%", height: "100%" }}
        />
        <div className="absolute top-3 left-3 bg-card/85 backdrop-blur-sm border border-border p-2.5 rounded-md shadow-sm text-xs pointer-events-none max-w-[230px]">
          <div className="font-semibold text-foreground border-b border-border pb-1 mb-1 flex items-center gap-1.5">
            <span className="inline-block w-2.5 h-2.5 rounded-full bg-primary animate-pulse" />
            Free 3D Orbit
          </div>
          <div className="text-muted-foreground leading-snug">
            Drag to orbit (rotate under the surface)<br />
            Scroll to zoom · right-drag to pan
          </div>
        </div>
        {terrainPending && (
          <div className="absolute bottom-3 left-1/2 -translate-x-1/2 flex items-center gap-2 bg-card/90 backdrop-blur-sm border border-border px-3 py-2 rounded-md shadow-sm text-xs text-foreground">
            <span className="inline-block w-3.5 h-3.5 rounded-full border-2 border-primary border-t-transparent animate-spin" />
            {terrainMesh ? "Refining terrain…" : "Sampling USGS 3DEP terrain…"}
          </div>
        )}
      </div>

      {/* Control sidebar + geologic legend */}
      <div className="w-full md:w-[320px] bg-background border-t md:border-t-0 md:border-l border-border p-4 flex flex-col gap-4 overflow-y-auto h-[240px] md:h-full">
        <div className="border-b border-border pb-3">
          <h3 className="font-semibold text-xs text-foreground uppercase tracking-wider mb-2.5">Display Settings</h3>
          <div className="flex flex-col gap-2 text-xs">
            <label className="flex items-center gap-2 text-foreground cursor-pointer">
              <input type="checkbox" checked={showUnits} onChange={(e) => setShowUnits(e.target.checked)} className="rounded border-border text-primary focus:ring-primary" />
              <span>Stratigraphic units (3D)</span>
            </label>
            <label className="flex items-center gap-2 text-foreground cursor-pointer">
              <input type="checkbox" checked={showLines} onChange={(e) => setShowLines(e.target.checked)} className="rounded border-border text-primary focus:ring-primary" />
              <span>Contacts &amp; faults (3D)</span>
            </label>
            {sheetImg && (
              <label className="flex items-center gap-2 text-foreground cursor-pointer">
                <input type="checkbox" checked={showSheet} onChange={(e) => setShowSheet(e.target.checked)} className="rounded border-border text-primary focus:ring-primary" />
                <span>Geologic map sheet (draped)</span>
              </label>
            )}
          </div>
          <p className="mt-2 text-[10px] text-muted-foreground">{terrainMesh ? "Map sheet drapes the USGS 3DEP terrain (1 m lidar); fence tops meet the ground." : "Sampling USGS 3DEP terrain…"}</p>
        </div>

        <div className="border-b border-border pb-3">
          <div className="flex justify-between items-center text-xs mb-1.5">
            <span className="font-semibold text-foreground uppercase tracking-wider">Vertical Exaggeration</span>
            <span className="text-muted-foreground font-mono">{vex.toFixed(1)}x</span>
          </div>
          <input type="range" min="0.5" max="5.0" step="0.1" value={vex} onChange={(e) => setVex(parseFloat(e.target.value))}
            className="w-full h-1.5 rounded-lg bg-muted appearance-none cursor-pointer accent-primary" />
        </div>

        <div className="flex-1 flex flex-col min-h-0">
          <div className="flex justify-between items-center text-xs mb-2">
            <h3 className="font-semibold text-foreground uppercase tracking-wider">Geologic Legend</h3>
            <span className="text-[10px] text-muted-foreground font-mono">{legend.length} units</span>
          </div>
          <input type="text" placeholder="Filter units..." value={search} onChange={(e) => setSearch(e.target.value)}
            className="w-full text-xs border border-border bg-card px-2.5 py-1.5 rounded mb-2.5 focus:outline-none focus:border-primary" />
          <div className="flex-1 overflow-y-auto pr-1 flex flex-col gap-1.5 max-h-[220px] md:max-h-none">
            {legend
              .filter((l) => !search || l.unit.toLowerCase().includes(search.toLowerCase()) || l.label.toLowerCase().includes(search.toLowerCase()))
              .map((l) => (
                <div key={l.unit} onMouseEnter={() => setHovered(l.unit)} onMouseLeave={() => setHovered(null)}
                  className={`flex items-start gap-2.5 p-1.5 rounded border text-xs cursor-default transition ${hovered === l.unit ? "border-primary bg-primary/5 font-medium" : "border-transparent hover:bg-muted"}`}>
                  <span className="inline-block w-4 h-4 rounded border border-black/10 shrink-0" style={{ backgroundColor: colorOf(l.unit) }} />
                  <div className="flex-1 leading-snug">
                    <span className="font-bold font-mono mr-1.5">{l.label}</span>
                    <span className="text-foreground">{l.unit}</span>
                  </div>
                </div>
              ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function AssetPane({ kind, asset, item }: { kind: AssetKind; asset: Asset; item: StacDoc }) {
  switch (kind) {
    case "cog": return <PreviewMapSlot spec={{ kind: "cog", item, href: asset.href }} />;
    case "threeD": return <ThreeDViewer asset={asset} item={item} />;
    case "parquet": return <DataExplorer href={asset.href} />;
    case "image":
      return (
        <img src={asset.href} alt={asset.title ?? "image"} loading="lazy"
          className="mt-2 max-h-[600px] w-auto max-w-full rounded-md border border-border bg-muted object-contain" />
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
        className="mt-2 h-[400px] w-full max-w-[1100px] rounded-md border border-border sm:h-[640px]">
        <div className="p-3 text-xs text-muted-foreground">
          Can’t embed this PDF — <a href={asset.href} target="_blank" rel="noopener" className="text-primary">open it ↗</a>
        </div>
      </object>
    );
  }
  return (
    <div className="mt-2 max-w-[1100px]">
      <button onClick={() => setShow(true)} title="Load the full PDF preview"
        className="group relative block w-full overflow-hidden rounded-md border border-border bg-muted">
        {poster
          ? <img src={poster} alt={asset.title ?? "PDF cover"} className="max-h-[640px] w-full object-contain" />
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
      || KIND_RANK[x.kind] - KIND_RANK[y.kind]), [item.assets]);
  const tabs = entries.filter((e) => e.kind !== "other");
  const others = entries.filter((e) => e.kind === "other");
  const [activeKey, setActiveKey] = useState<string | undefined>(tabs[0]?.key);
  useEffect(() => { setActiveKey(tabs[0]?.key); }, [item.id]);   // reset on item change

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
function Preview({ item }: { item: StacDoc }) {
  if (pmtilesLink(item)) return <VectorPreview item={item} />;
  if (rasterTilesAsset(item)) return <RasterMosaicPreview item={item} />;
  return <AssetViewer item={item} />;
}

// Property key/value formatting for the detail table — drop the `ugs:` prefix, underscores →
// spaces; join arrays, stringify objects, so nothing renders as `[object Object]` or overflows.
const prettyKey = (k: string) => k.replace(/^ugs:/, "").replace(/_/g, " ");
const fmtVal = (v: unknown): string =>
  Array.isArray(v) ? v.join(", ") : v && typeof v === "object" ? JSON.stringify(v) : String(v);

// ---- API & data endpoints ----
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
      className="rounded border border-border bg-card px-1.5 py-0.5 text-[11px] text-foreground hover:border-primary">
      {done ? "copied" : "copy"}
    </button>
  );
}

const ducklakeAsset = (item: StacDoc): Asset | undefined =>
  Object.entries(item.assets ?? {}).find(([k, a]) => k === "ducklake"
    || a.roles?.includes("ducklake") || a.href.includes("ducklake"))?.[1];

// All the ways out to the data: the OGC API Features service (= the modern WFS — REST + GeoJSON,
// served by featureserv), plus the raw artifacts (GeoParquet, PMTiles, DuckLake). Replaces the
// lone "OGC API" link that dumped users on a featureserv page with no hint of the other endpoints.
function EndpointsPanel({ item }: { item: StacDoc }) {
  const id = String(item.id ?? "");
  const coll = featuresCollectionUrl(id);
  const pq = parquetAsset(item);
  const pm = pmtilesLink(item);
  const ducklake = ducklakeAsset(item);
  const rows: { label: string; desc: string; url: string }[] = [];
  if (coll) {
    rows.push({ label: "OGC API Features", desc: "REST feature service — collection metadata", url: coll });
    rows.push({ label: "Features (GeoJSON)", desc: "Query features as GeoJSON (paged)", url: `${coll}/items?limit=50` });
  }
  if (pq) rows.push({ label: "GeoParquet", desc: "Columnar file — DuckDB / GeoPandas / QGIS", url: pq.href });
  if (pm) rows.push({ label: "PMTiles", desc: "Vector tiles for web maps", url: pm.href });
  if (ducklake) rows.push({ label: "DuckLake", desc: "Lakehouse table", url: ducklake.href });
  if (!rows.length) return null;
  return (
    <div className="mt-3 rounded-lg border border-border bg-muted p-3">
      <div className="mb-1.5 text-xs font-semibold text-muted-foreground">API &amp; data endpoints</div>
      <div className="flex flex-col gap-1.5">
        {rows.map((r) => (
          <div key={r.label} className="flex flex-wrap items-center gap-2 text-xs">
            <span className="w-36 shrink-0 font-semibold text-foreground" title={r.desc}>{r.label}</span>
            <code className="min-w-0 flex-1 truncate rounded bg-card px-1.5 py-0.5 text-[11px] text-muted-foreground" title={r.url}>{r.url}</code>
            <CopyBtn text={r.url} />
            <a href={r.url} target="_blank" rel="noopener" className="text-primary no-underline">open ↗</a>
          </div>
        ))}
      </div>
    </div>
  );
}

// ---- item detail ----
// A related item's STAC .json href → a viewer deep-link (?c=<collection>&i=<id>).
const relatedViewerHref = (stacHref: string): string => {
  const m = stacHref.match(/\/([^/]+)\/([^/]+)\/[^/]+\.json(?:\?.*)?$/);
  return m ? `?c=${encodeURIComponent(m[1])}&i=${encodeURIComponent(m[2])}` : stacHref;
};

// Registry-driven relationships (FK graph): related layers, this layer's references, related tables.
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
    <section className={`mt-4 rounded-md border border-border p-3 ${openTables.size || openGalleries.size ? "max-w-none" : "max-w-3xl"}`}>
      <h3 className="text-sm font-semibold">Related</h3>
      {links.length > 0 && (
        <div className="mt-1.5">
          <div className="text-[11px] uppercase tracking-wide text-muted-foreground">Related layers</div>
          <ul className="mt-1 space-y-0.5">
            {links.map((l, i) => (
              <li key={i}><a href={relatedViewerHref(l.href)} className="text-primary hover:underline">{l.title ?? "related"} ›</a></li>
            ))}
          </ul>
        </div>
      )}
      {fks.length > 0 && (
        <div className="mt-2">
          <div className="text-[11px] uppercase tracking-wide text-muted-foreground">This layer references</div>
          <ul className="mt-1 space-y-0.5 text-xs">
            {fks.map((fk, i) => (
              <li key={i}><code>{fk.fields.join(", ")}</code> → <span className="font-medium">{humanize(fk.reference.resource)}</span>.<code>{fk.reference.fields.join(", ")}</code></li>
            ))}
          </ul>
        </div>
      )}
      {tables.length > 0 && (
        <div className="mt-2">
          <div className="text-[11px] uppercase tracking-wide text-muted-foreground">Related tables</div>
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
                {openTables.has(key) && <DataExplorer href={asset.href} />}
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
    <section className="mt-4 max-w-3xl rounded-md border border-amber-500/40 bg-amber-500/[0.04] p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold">Review</h3>
        <LayerStatusControl itemId={id} />
      </div>
      {geoparquet && <DiffPanel stem={id} reviewParquetUrl={geoparquet} />}
      <CommentsPanel itemId={id} />
      {cols && cols.length > 0 && (
        <div className="mt-3">
          <div className="mb-1 text-[11px] uppercase tracking-wide text-muted-foreground">Columns</div>
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

function ItemDetail({ collectionId, item, onBack, onMap }: {
  collectionId: string; item?: StacDoc; onBack: () => void; onMap: () => void;
}) {
  if (!item) return <em className={C.muted}>Loading…</em>;
  const p = item.properties ?? {};
  const hasGeom = Boolean(item.geometry || item.bbox);
  const via = viaLink(item);
  const cite = citeLink(item);
  return (
    <>
      <div className="mb-2.5">
        <span className={C.crumb} onClick={onBack}>{collectionId}</span>
        <span className={C.muted}> / {item.id}</span>
      </div>
      <div className="font-mono text-sm font-semibold text-primary">{item.id}</div>
      <h2 className="mb-1 text-xl font-semibold">{String(p.title ?? item.id ?? "")}</h2>
      {typeof p.description === "string" && <p className="max-w-3xl text-muted-foreground">{p.description}</p>}
      <Preview item={item} />
      {item.assets && <div className="my-2"><AssetChips assets={item.assets} /></div>}
      <div className="mt-1.5 flex flex-wrap gap-2">
        {hasGeom && (
          <button onClick={onMap}
            className="inline-block rounded bg-emerald-700 px-2.5 py-1 text-[11px] text-white hover:bg-emerald-800">
            View on map ›
          </button>
        )}
        {via && (
          <a href={via.href} target="_blank" rel="noopener"
            className="inline-block rounded bg-primary px-2.5 py-1 text-[11px] text-primary-foreground no-underline hover:opacity-90">
            {via.title ?? "Publication page"} ↗
          </a>
        )}
        {cite && (
          <a href={cite.href} target="_blank" rel="noopener"
            className="inline-block rounded border border-border px-2.5 py-1 text-[11px] text-foreground no-underline hover:border-primary">
            Cite (DOI) ↗
          </a>
        )}
      </div>
      <IssueContents item={item} />
      <ExportPanel item={item} />
      <EndpointsPanel item={item} />
      <RelatedPanel item={item} />
      {IS_REVIEW && <CatalogReview item={item} />}
      <table className="mt-3 w-full max-w-3xl table-fixed border-collapse text-sm">
        <tbody>
          {Object.entries(p)
            .filter(([k, v]) => v !== null && v !== "" && k !== "ugs:renders" && k !== "ugs:contents")
            .map(([k, v]) => (
              <tr key={k}>
                <td className="w-44 break-words border-b border-border px-2.5 py-1 align-top text-muted-foreground">{prettyKey(k)}</td>
                <td className="break-words border-b border-border px-2.5 py-1 align-top">{fmtVal(v)}</td>
              </tr>
            ))}
        </tbody>
      </table>
    </>
  );
}

export function Browse(props: {
  cards: CollectionSummary[];
  collectionId?: string;
  allItems: ItemRef[];
  itemsLoading: boolean;
  showItems: boolean;
  atRoot: boolean;
  breadcrumb: { label: string; onClick?: () => void }[];
  search: string;
  onSearch: (q: string) => void;
  threeD: boolean;
  onThreeD: (v: boolean) => void;
  browseAll: boolean;
  onBrowseAll: (v: boolean) => void;
  layerCollectionIds: string[];
  series: string[];
  onSeries: (codes: string[]) => void;
  item?: StacDoc;
  itemSelected: boolean;
  onOpenCollection: (href: string) => void;
  onOpenItem: (href: string) => void;
  onOpenCover: (href: string) => void;
  onBackToItems: () => void;
  onViewMap: () => void;
}) {
  const { collectionId, itemSelected, showItems, atRoot, search, onSearch, threeD, onThreeD,
          browseAll, onBrowseAll, series, onSeries } = props;

  // item detail
  if (collectionId && itemSelected) {
    return (
      <div className={C.wrap}>
        <ItemDetail collectionId={collectionId} item={props.item}
          onBack={props.onBackToItems} onMap={props.onViewMap} />
      </div>
    );
  }

  // a leaf collection's items
  if (showItems && collectionId) {
    const items = props.allItems.filter((it) => it.collId === collectionId);
    return (
      <div className={C.wrap}>
        <Breadcrumb crumbs={props.breadcrumb} />
        {props.itemsLoading && <span className={C.muted}>loading items…</span>}
        <ItemList items={items} onOpen={props.onOpenItem} series={series} onSeries={onSeries} />
      </div>
    );
  }

  // Drop map-layer collections from the by-date list: their datetime is ingest time, not a pub date.
  // App derives the set from catalog structure, so future layer collections are excluded automatically.
  const layerColls = new Set(props.layerCollectionIds);
  const globalItems = browseAll && !search.trim() && !threeD
    ? props.allItems.filter((it) => !layerColls.has(it.collId))
    : props.allItems;

  // browse level: root catalog (with search-all) OR a sub-catalog's series chooser
  return (
    <div className={C.wrap}>
      {!atRoot && <Breadcrumb crumbs={props.breadcrumb} />}
      {atRoot && (
        <div className={C.bar}>
          <input className={C.input} placeholder="Search all collections…" value={search}
            onChange={(e) => onSearch(e.target.value)} />
          {/* Global 3D discovery: loads every collection's items (like a search) + filters to those
              carrying a 3d-vector asset — works from the bare catalog, no search text needed. */}
          <span className={toggle(threeD)} title="Show only publications with an interactive 3D viewer"
            onClick={() => onThreeD(!threeD)}>3D</span>
          {/* Flat catalog-wide list, newest first — answers "what's newest?" without drilling into
              every series. Same loads-all-items path as search; the list defaults to date-desc. */}
          <span className={toggle(browseAll)} title="One list of every publication across all series, newest first"
            onClick={() => onBrowseAll(!browseAll)}>All items</span>
          {props.itemsLoading && <span className={C.muted}>loading items…</span>}
        </div>
      )}
      {atRoot && (search.trim() || threeD || browseAll)
        ? <ItemList items={globalItems} showCollection query={search} force3D={threeD} onOpen={props.onOpenItem} series={series} onSeries={onSeries} />
        : <Collections collections={props.cards} heading={atRoot ? "Collections" : "Series"} onOpen={props.onOpenCollection} onOpenItem={props.onOpenCover} />}
    </div>
  );
}
