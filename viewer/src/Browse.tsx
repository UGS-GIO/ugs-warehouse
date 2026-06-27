// Catalog-centric browser: metadata over map. Collection cards (with counts) +
// search-all → sortable item table / cards → item detail. The map is one link out.
import {
  type ColumnDef, flexRender, getCoreRowModel, getSortedRowModel,
  type SortingState, useReactTable,
} from "@tanstack/react-table";
import maplibregl from "maplibre-gl";
import { useEffect, useMemo, useRef, useState } from "react";
import { Layer, type LayerProps, type MapLayerMouseEvent, Map as MapGL, type MapRef, NavigationControl, Popup, Source } from "react-map-gl/maplibre";
import { ensureCogProtocol } from "./cog";
import { type ColFilter, exportItem, type ExportFormat, FORMATS } from "./download";
import { Legend } from "./legend";
import { type Asset, citeLink, classificationEntries, cogAsset, defaultStyleUrl, featuresCollectionUrl, ownForeignKeys, pmtilesLink, relatedAssets, relatedLinks, rendersOf, type StacDoc, tableColumns, thumbnailAsset, viaLink } from "./stac";

export type CollectionSummary = {
  id: string; href: string; title?: string; description?: string;
  count?: number; mappable?: number; kind?: "catalog" | "collection"; parentId?: string;
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
// The STAC item id IS the publication series id (DS-8, OFR-647, …) / the layer stem.
const gSeries = (it: ItemRef) => String(it.data?.id ?? idFromHref(it.href));
const gTitle = (it: ItemRef) => String(props(it).title ?? it.data?.id ?? idFromHref(it.href));
const gDate = (it: ItemRef) => (typeof props(it).datetime === "string" ? (props(it).datetime as string).slice(0, 10) : "");
const gYear = (it: ItemRef): number | null => { const y = parseInt(gDate(it).slice(0, 4), 10); return Number.isFinite(y) ? y : null; };
const gType = (it: ItemRef) => String(props(it)["ugs:pub_type"] ?? props(it)["ugs:series"] ?? props(it)["ugs:topic"] ?? "");
const gScale = (it: ItemRef) => String(props(it)["ugs:scale"] ?? "");
const haystack = (it: ItemRef) => (it.href + JSON.stringify(it.data?.properties ?? {})).toLowerCase();
// "Mappable" = has something to draw on the map: a COG (raster) or PMTiles (vector). Items with
// neither (metadata-only pubs) do nothing when toggled — the filter hides them.
const hasMapData = (it: ItemRef) => !!(cogAsset(it.data) || pmtilesLink(it.data));
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
  if (!parquet) return null;

  const run = async (fmt: ExportFormat) => {
    setErr(undefined);
    setBusy(fmt);
    try {
      await exportItem(parquet.href, String(item.id ?? "export"), fmt, clipOn ? bbox : undefined);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
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
    </div>
  );
}

// ---- collection cards ----
const humanize = (id: string) =>
  id.replace(/^ugs-/, "").replace(/[-_]+/g, " ").replace(/\b\w/g, (ch) => ch.toUpperCase());
// The warehouse emits a placeholder "UGS warehouse — {id}." description; hide it as noise.
const meaningfulDesc = (d?: string) => (d && !/^UGS warehouse — .*\.$/.test(d) ? d : null);

function Collections({ collections, heading, onOpen }: {
  collections: CollectionSummary[]; heading: string; onOpen: (href: string) => void;
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

// ---- item list: filter + sort + table/cards, reused for a collection and global search ----
function ItemList({ items, showCollection, query, onOpen, series, onSeries }: {
  items: ItemRef[]; showCollection?: boolean; query?: string; onOpen: (href: string) => void;
  series: string[]; onSeries: (codes: string[]) => void;
}) {
  const [q, setQ] = useState("");
  const [mode, setMode] = useState<"table" | "cards" | "thumbs">("table");
  const [mapOnly, setMapOnly] = useState(false);  // hide metadata-only items (no COG / no tiles)
  const [yearMin, setYearMin] = useState("");
  const [yearMax, setYearMax] = useState("");
  const [topics, setTopics] = useState<string[]>([]);  // map-pub topic filter (multi-select)

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

  const needle = (query ?? q).trim().toLowerCase();
  const ymin = parseInt(yearMin, 10);
  const ymax = parseInt(yearMax, 10);
  const rows = useMemo(
    () => items.filter((it) => {
      if (needle && !haystack(it).includes(needle)) return false;
      if (sel.size && !sel.has(gCode(it))) return false;
      if (tsel.size && !tsel.has(gTopic(it))) return false;
      if (mapOnly && !hasMapData(it)) return false;
      if (Number.isFinite(ymin) || Number.isFinite(ymax)) {
        const y = gYear(it);
        if (y == null || (Number.isFinite(ymin) && y < ymin) || (Number.isFinite(ymax) && y > ymax)) return false;
      }
      return true;
    }),
    [items, needle, series, topics, mapOnly, yearMin, yearMax],
  );

  const columns = useMemo<ColumnDef<ItemRef, unknown>[]>(() => [
    { id: "id", header: "ID", accessorFn: gSeries, sortingFn: "alphanumeric",
      cell: (i) => <span className="whitespace-nowrap font-mono text-[13px] font-semibold text-foreground">{String(i.getValue())}</span> },
    { id: "title", header: "Title", accessorFn: gTitle,
      cell: (i) => <span className="text-primary">{String(i.getValue())}</span> },
    ...(showCollection ? [{ id: "collection", header: "Collection", accessorFn: (it: ItemRef) => it.collId }] : []),
    { id: "type", header: "Type", accessorFn: gType },
    { id: "date", header: "Date", accessorFn: gDate },
    { id: "scale", header: "Scale", accessorFn: gScale, enableSorting: false },
    { id: "assets", header: "Assets", enableSorting: false, accessorFn: () => "",
      cell: ({ row }) => row.original.data?.assets ? <AssetChips assets={row.original.data.assets} /> : "" },
  ], [showCollection]);

  return (
    <>
      <div className={C.bar}>
        {query === undefined && (
          <input className={C.input} placeholder="Filter…" value={q} onChange={(e) => setQ(e.target.value)} />
        )}
        <span className={C.muted}>{rows.length} of {items.length}</span>
        <span className="flex-1" />
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
        <div className="overflow-x-auto">
          <DataTable columns={columns} data={rows} onRowClick={(it) => onOpen(it.href)}
            initialSorting={[{ id: "id", desc: false }]} />
        </div>
      ) : mode === "thumbs" ? (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 md:grid-cols-4 lg:grid-cols-6">
          {rows.map((it) => {
            const th = thumbnailAsset(it.data);
            return (
              <div key={it.href} onClick={() => onOpen(it.href)}
                className="cursor-pointer overflow-hidden rounded-md border border-border bg-card hover:ring-1 hover:ring-primary">
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
          })}
        </div>
      ) : (
        <div className={C.grid}>
          {rows.map((it) => (
            <div key={it.href} className={C.card} onClick={() => onOpen(it.href)}>
              <div className="font-mono text-[12px] font-semibold text-foreground">{gSeries(it)}</div>
              <p className={C.cardTitle}>{gTitle(it)}</p>
              <div>
                {showCollection && <span className={C.badge}>{it.collId}</span>}
                {gDate(it) && <span className={C.badge}>{gDate(it)}</span>}
                {BADGE_KEYS.filter((k) => props(it)[k]).map((k) => (
                  <span key={k} className={C.badge}>{String(props(it)[k])}</span>
                ))}
              </div>
              {it.data?.assets && <div><AssetChips assets={it.data.assets} /></div>}
            </div>
          ))}
        </div>
      )}
    </>
  );
}

const POSITRON = "https://tiles.openfreemap.org/styles/positron";

const bboxPolygon = (b: number[]): GeoJSON.Polygon => {
  const [w, s, e, n] = b;
  return { type: "Polygon", coordinates: [[[w, s], [e, s], [e, n], [w, n], [w, s]]] };
};

const asBounds = (item: StacDoc): [[number, number], [number, number]] | undefined => {
  const b = item.bbox?.slice(0, 4);
  return b && b.length === 4 ? [[b[0], b[1]], [b[2], b[3]]] : undefined;
};

// Interactive COG explorer — the actual georeferenced raster (real cartography), range-read
// + decoded client-side. No server, no invented styling. Pannable/zoomable.
function CogMap({ href, item }: { href: string; item: StacDoc }) {
  const [ready, setReady] = useState(false);
  const mapRef = useRef<MapRef>(null);
  const cogBbox = useRef<[number, number, number, number] | undefined>(undefined);

  // Fit to the COG's own extent (most items here have no STAC footprint, so the COG metadata
  // bbox is the only reliable extent); fall back to the STAC bbox.
  const fit = () => {
    const b = cogBbox.current ?? (item.bbox?.slice(0, 4) as [number, number, number, number] | undefined);
    if (b && mapRef.current) mapRef.current.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: 16, duration: 0 });
  };

  useEffect(() => {
    let live = true;
    (async () => {
      await ensureCogProtocol();
      if (!live) return;
      setReady(true);
      try {
        const { getCogMetadata } = await import("@geomatico/maplibre-cog-protocol");
        const meta = await getCogMetadata(href);
        if (live && meta?.bbox) { cogBbox.current = meta.bbox; fit(); }
      } catch { /* keep STAC bbox / default view */ }
    })();
    return () => { live = false; };
  }, [href]);

  return (
    <div className="mt-2 h-96 w-full max-w-[1100px] overflow-hidden rounded-md border border-border bg-muted">
      {ready
        ? (
          <MapGL
            ref={mapRef}
            mapLib={maplibregl}
            initialViewState={{ longitude: -111.7, latitude: 39.3, zoom: 6 }}
            mapStyle={POSITRON}
            style={{ width: "100%", height: "100%" }}
            onLoad={fit}
          >
            <NavigationControl position="top-right" showCompass={false} />
            <Source id="cog" type="raster" url={`cog://${href}`} tileSize={256}>
              <Layer id="cog-raster" type="raster" />
            </Source>
          </MapGL>
        )
        : <div className="flex h-full items-center justify-center text-xs text-muted-foreground">loading COG…</div>}
    </div>
  );
}

// Neutral, geometry-agnostic render used until a ugs-styles style is bound — visible borders
// (not faux cartography): light fill, clear outline, points. fill/line/circle all added so any
// geometry type shows.
const NEUTRAL_LAYERS = [
  { type: "fill", filter: ["==", ["geometry-type"], "Polygon"],
    paint: { "fill-color": "#6b7280", "fill-opacity": 0.15, "fill-outline-color": "#374151" } },
  { type: "line", filter: ["match", ["geometry-type"], ["LineString", "Polygon"], true, false],
    paint: { "line-color": "#374151", "line-width": 1.1 } },
  // circles only on actual point features — else maplibre dots every polygon/line vertex.
  { type: "circle", filter: ["==", ["geometry-type"], "Point"],
    paint: { "circle-color": "#374151", "circle-radius": 3.5, "circle-opacity": 0.85 } },
];

// Interactive vector preview — the item's actual PMTiles features. Uses the bound ugs-styles
// GL style (via the render extension) when present; else a neutral geometry render (no
// invented cartography — real styling arrives through `renders`).
// `key` identifies the SELECTION (row offset / feature id), so the map re-flies on every distinct
// pick — even two features at the same lat/lon (identical bbox). The bbox/geometry upgrade within
// one pick reuses the same key, so it doesn't double-fly.
type FocusSel = { bbox?: [number, number, number, number]; geometry?: GeoJSON.Geometry | null; key?: string | number };

// Load a baked sprite sheet (pie-wedge icons for box-type) into the map via addImage, slicing each
// frame from the PNG so icon-image names match the style as authored (no sprite-id namespacing).
// Picks @2x on retina. Idempotent (skips images already added).
async function loadSpriteImages(map: maplibregl.Map, base: string): Promise<void> {
  const hi = (window.devicePixelRatio || 1) >= 1.5 ? "@2x" : "";
  const [index, blob] = await Promise.all([
    fetch(`${base}${hi}.json`).then((r) => r.json()),
    fetch(`${base}${hi}.png`).then((r) => r.blob()),
  ]);
  const sheet = await createImageBitmap(blob);
  for (const [name, f] of Object.entries(index as Record<string, { x: number; y: number; width: number; height: number; pixelRatio: number }>)) {
    if (map.hasImage(name)) continue;
    const img = await createImageBitmap(sheet, f.x, f.y, f.width, f.height);
    map.addImage(name, img, { pixelRatio: f.pixelRatio });
  }
}

function PmtilesMap({ item, focus, onFeatureClick }: {
  item: StacDoc; focus?: FocusSel | null;
  onFeatureClick?: (featureId: number, props: Record<string, unknown>) => void;
}) {
  const pm = pmtilesLink(item);
  const renders = useMemo(() => rendersOf(item), [item]);
  const renderKeys = Object.keys(renders);
  // Selected render: prefer `default`, else the first; lets a multi-render layer (wells:
  // by-purpose / by-boxtype) switch symbology.
  const [sel, setSel] = useState<string>(() => (renders.default ? "default" : renderKeys[0] ?? ""));
  useEffect(() => { setSel(renders.default ? "default" : Object.keys(renders)[0] ?? ""); }, [item.id]);
  const active = renders[sel];
  const styleUrl = active?.style_url ?? defaultStyleUrl(item);
  const sprite = active?.sprite;

  const mapRef = useRef<MapRef>(null);
  const [mapLoaded, setMapLoaded] = useState(false);
  const [styleLayers, setStyleLayers] = useState<Record<string, unknown>[] | null>(null);
  const [spriteReady, setSpriteReady] = useState(false);
  const [popup, setPopup] = useState<{ lng: number; lat: number; props: Record<string, unknown> } | null>(null);
  // Close the popup when the item changes (a stale popup over a different layer would mislead).
  useEffect(() => { setPopup(null); }, [item.id]);

  useEffect(() => {
    setStyleLayers(null);  // clear immediately so the prior render's layers don't linger on switch
    if (!styleUrl) return;
    let live = true;
    fetch(styleUrl).then((r) => r.json())
      .then((d) => { if (live) setStyleLayers(Array.isArray(d?.layers) ? d.layers : null); })
      .catch(() => { if (live) setStyleLayers(null); });
    return () => { live = false; };
  }, [styleUrl]);

  // Preload the render's sprite (icon renders only) before its symbol layers mount, so icons
  // resolve instead of flashing missing. Non-sprite renders are "ready" immediately.
  useEffect(() => {
    if (!sprite) { setSpriteReady(true); return; }
    setSpriteReady(false);
    const map = mapRef.current?.getMap();
    if (!map || !mapLoaded) return;
    let live = true;
    loadSpriteImages(map, sprite)
      .then(() => { if (live) setSpriteReady(true); })
      .catch(() => { if (live) setSpriteReady(true); });
    return () => { live = false; };
  }, [sprite, mapLoaded]);

  // Fly to the picked feature (bbox from the parquet covering columns). maxZoom keeps a point
  // (degenerate bbox) from zooming to street level. Keyed on the selection `key` (not bbox values)
  // so picking a DIFFERENT feature at the same lat/lon still re-flies; the geometry upgrade within
  // one pick keeps the same key, so it doesn't double-fly.
  const fb = focus?.bbox;
  const focusKey = focus?.key;
  useEffect(() => {
    if (!fb || !mapRef.current) return;
    mapRef.current.fitBounds([[fb[0], fb[1]], [fb[2], fb[3]]], { padding: 60, maxZoom: 14, duration: 800 });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusKey]);

  if (!pm) return null;
  const sourceLayer = pm["pmtiles:layers"]?.[0] ?? String(item.id ?? "");
  const bounds = asBounds(item);
  // Highlight the REAL feature geometry once fetched; until then (or if unavailable) fall back to
  // the bbox outline so the click gives instant feedback. One source, three layers — line/fill for
  // polygons & lines, circle for points (a layer whose type doesn't match the geom renders nothing).
  const hlGeom: GeoJSON.Geometry | null = focus?.geometry ?? (fb ? bboxPolygon(fb) : null);
  // While an icon render's sprite is still loading, render no style layers (avoids a missing-icon
  // flash + an id-reuse "layer type changed" swap). Layer ids include `sel` so switching renders
  // remounts cleanly (different type on the same id otherwise throws in maplibre).
  const layers = sprite && !spriteReady ? [] : (styleLayers ?? NEUTRAL_LAYERS);
  // Stable ids for the rendered style layers (also the click targets). Computed once so the
  // <Layer> loop and interactiveLayerIds agree exactly.
  const layerIds = layers.map((l, i) => `pm-${sel}-${(l as { id?: string }).id ?? i}`);
  // Click a feature → popup with its attributes + bubble its feature_id up so the table can page
  // to + highlight the matching row. `f.id` is the native MVT feature id (= the transform's
  // feature_id, via tippecanoe --use-attribute-for-id); undefined on pre-reingest tiles → no-op.
  const onMapClick = (e: MapLayerMouseEvent) => {
    const f = e.features?.[0];
    if (!f) { setPopup(null); return; }
    const props = (f.properties ?? {}) as Record<string, unknown>;
    setPopup({ lng: e.lngLat.lng, lat: e.lngLat.lat, props });
    if (f.id != null) onFeatureClick?.(Number(f.id), props);
  };
  return (
    <>
      {renderKeys.length > 1 && (
        <div className="mb-1.5 flex items-center gap-2 text-xs">
          <span className="text-muted-foreground">Symbolize by</span>
          <select value={sel} onChange={(e) => setSel(e.target.value)}
            className="rounded border border-input bg-card px-2 py-1 text-foreground">
            {renderKeys.map((k) => <option key={k} value={k}>{renders[k].title ?? k}</option>)}
          </select>
        </div>
      )}
      <div className="mt-2 h-96 w-full max-w-[1100px] overflow-hidden rounded-md border border-border bg-muted">
        <MapGL
          ref={mapRef}
          mapLib={maplibregl}
          onLoad={() => setMapLoaded(true)}
          initialViewState={bounds ? { bounds, fitBoundsOptions: { padding: 16 } } : { longitude: -111.7, latitude: 39.3, zoom: 6 }}
          mapStyle={POSITRON}
          interactiveLayerIds={onFeatureClick ? layerIds : undefined}
          onClick={onFeatureClick ? onMapClick : undefined}
          style={{ width: "100%", height: "100%" }}
        >
          <NavigationControl position="top-right" showCompass={false} />
          <Source id="pm-prev" type="vector" url={`pmtiles://${pm.href}`} />
          {layers.map((l, i) => {
            // id from the fragment's OWN layer id (e.g. …-circle vs …-boxtype) so switching renders
            // never reuses an id with a different `type` (maplibre throws "layer type changed" and
            // the swap silently fails). `sel` prefix keeps renders fully disjoint. explicit
            // `source`/`source-layer` — react-map-gl won't inject them into an array.
            const lid = layerIds[i];
            return <Layer key={lid} {...({ ...l, id: lid, source: "pm-prev", "source-layer": sourceLayer } as unknown as LayerProps)} />;
          })}
          {/* Picked-row highlight — the real feature geometry (line/fill/circle by geom type). */}
          {hlGeom && (
            <Source id="pm-hl" type="geojson" data={{ type: "Feature", properties: {}, geometry: hlGeom }}>
              <Layer id="pm-hl-fill" type="fill" paint={{ "fill-color": "#f59e0b", "fill-opacity": 0.25 }} />
              <Layer id="pm-hl-line" type="line" paint={{ "line-color": "#f59e0b", "line-width": 3 }} />
              <Layer id="pm-hl-pt" type="circle" paint={{ "circle-radius": 7, "circle-color": "#f59e0b", "circle-stroke-color": "#fff", "circle-stroke-width": 2 }} />
            </Source>
          )}
          {popup && (
            <Popup longitude={popup.lng} latitude={popup.lat} onClose={() => setPopup(null)} closeButton maxWidth="320px">
              <div className="max-h-56 overflow-auto">
                <table className="border-collapse text-[11px]">
                  <tbody>
                    {Object.entries(popup.props).filter(([, v]) => v !== null && v !== "").map(([k, v]) => (
                      <tr key={k}>
                        <td className="whitespace-nowrap py-0.5 pr-2 align-top text-gray-500">{k}</td>
                        <td className="py-0.5 text-gray-900">{String(v)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Popup>
          )}
        </MapGL>
      </div>
      {/* Legend follows the active render: explicit entries for icon renders (box-type pie wedges),
          else derived from the style's paint. */}
      {/* Legend source order: explicit render legend (icon renders) → STAC classification:classes
          (post-reingest) → derived from the GL style's paint (the pre-reingest fallback). */}
      <Legend layers={styleLayers ?? undefined} entries={active?.legend ?? classificationEntries(item)}
        title={active?.legend ? "box type" : undefined}
        name={String(item.properties?.title ?? item.id)} />
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

// Vector asset preview: PMTiles map + full dataset explorer, linked — click a table row and the
// map flies to that feature (when the parquet carries bbox covering columns).
function VectorPreview({ item }: { item: StacDoc }) {
  const pq = parquetAsset(item);
  const [focus, setFocus] = useState<FocusSel | null>(null);
  // A map-feature click → {id, nonce}. The nonce makes re-clicking the SAME feature re-fire the
  // explorer effect (a bare id wouldn't change). The explorer pages to + highlights that row.
  const [pick, setPick] = useState<{ id: number; nonce: number } | null>(null);
  const onFeatureClick = (id: number) => setPick((p) => ({ id, nonce: (p?.nonce ?? 0) + 1 }));
  return (
    <>
      <PmtilesMap item={item} focus={focus} onFeatureClick={pq ? onFeatureClick : undefined} />
      <FieldsPanel item={item} />
      {pq && <DataExplorer href={pq.href} onPick={setFocus} mapPick={pick} />}
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
function DataExplorer({ href, onPick, mapPick }: {
  href: string; onPick?: (sel: FocusSel) => void;
  mapPick?: { id: number; nonce: number } | null;
}) {
  const [pageIndex, setPageIndex] = useState(0);
  const [sorting, setSorting] = useState<SortingState>([]);
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
      limit: PAGE_SIZE, offset: pageIndex * PAGE_SIZE,
      orderBy: sort?.id, desc: sort?.desc, search: applied.search, filters: applied.filters,
    }))
      .then((d) => { if (live) { setPage(d); setErr(undefined); } })
      .catch((e) => { if (live) setErr(e instanceof Error ? e.message : String(e)); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [href, pageIndex, sort?.id, sort?.desc, applied.search, filterKey]);

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
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const btn = "rounded border border-border bg-card px-2 py-0.5 text-xs text-foreground hover:border-primary disabled:opacity-40";
  const fIn = "w-full min-w-[64px] rounded border border-input bg-card px-1 py-0.5 text-[11px] font-normal normal-case text-foreground";
  const hasFilters = Boolean(search) || applied.filters.length > 0
    || Object.values(draft).some((d) => d.min || d.max || d.text);
  const clearAll = () => { setSearch(""); setDraft({}); };

  // Row click → zoom + highlight. Fire the bbox immediately (instant feedback), then fetch the
  // real geometry (same filter+sort, offset = page start + row index) and upgrade the highlight.
  const pick = (i: number, bbox: [number, number, number, number]) => {
    if (!onPick) return;
    const offset = pageIndex * PAGE_SIZE + i;
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
      if (live && pos != null) setPageIndex(Math.floor(pos / PAGE_SIZE));
    })();
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mapPick?.nonce]);

  return (
    <div className="mt-2">
      <div className="mb-1.5 flex flex-wrap items-center gap-2">
        <input className={C.input} placeholder="Search all columns…" value={search}
          onChange={(e) => setSearch(e.target.value)} />
        <span className={C.muted}>
          {page ? `${total.toLocaleString()} row${total === 1 ? "" : "s"}` : "…"}{loading ? " · loading" : ""}
          {onPick && page?.bboxes.some(Boolean) ? " · click a row to zoom" : ""}
        </span>
        {hasFilters && <button className="text-xs text-primary" onClick={clearAll}>clear filters</button>}
      </div>
      {err && <div className="mb-1.5 text-xs text-destructive">explorer failed: {err}</div>}
      <div className="max-w-full overflow-x-auto rounded-md border border-border text-[12px]">
        <table className="w-full border-collapse">
          <thead>
            {table.getHeaderGroups().map((hg) => (
              <tr key={hg.id}>
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
            {table.getRowModel().rows.map((r) => {
              const bbox = page?.bboxes[r.index] ?? null;
              const clickable = Boolean(onPick && bbox);
              const fid = r.original.feature_id;
              const hl = fid != null && Number(fid) === highlightId;
              return (
                <tr key={r.id}
                  className={`${hl ? "bg-amber-100 dark:bg-amber-900/40" : ""} ${clickable ? "cursor-pointer hover:bg-muted" : ""}`.trim() || undefined}
                  title={clickable ? "Zoom to feature on map" : undefined}
                  onClick={clickable ? () => { pick(r.index, bbox!); if (fid != null) setHighlightId(Number(fid)); } : undefined}>
                  {r.getVisibleCells().map((c) => (
                    <td key={c.id} className={C.td}>{flexRender(c.column.columnDef.cell, c.getContext())}</td>
                  ))}
                </tr>
              );
            })}
            {!loading && total === 0 && (
              <tr><td className="px-2.5 py-2 text-muted-foreground" colSpan={Math.max(1, columns.length)}>No rows match.</td></tr>
            )}
          </tbody>
        </table>
      </div>
      <div className="mt-1.5 flex items-center gap-1.5 text-xs">
        <button className={btn} disabled={pageIndex === 0} onClick={() => setPageIndex(0)}>«</button>
        <button className={btn} disabled={pageIndex === 0} onClick={() => setPageIndex((i) => i - 1)}>‹ Prev</button>
        <span className="px-1 text-muted-foreground">Page {pageIndex + 1} of {pageCount}</span>
        <button className={btn} disabled={pageIndex + 1 >= pageCount} onClick={() => setPageIndex((i) => i + 1)}>Next ›</button>
        <button className={btn} disabled={pageIndex + 1 >= pageCount} onClick={() => setPageIndex(pageCount - 1)}>»</button>
      </div>
    </div>
  );
}

// Non-interactive footprint locator — the item's geometry/bbox over a basemap. Fallback preview
// for items with no previewable file (or as a standalone where a map adds context).
function FootprintMini({ item }: { item: StacDoc }) {
  const bbox = item.bbox?.slice(0, 4) as [number, number, number, number] | undefined;
  const geom = item.geometry ?? (bbox ? bboxPolygon(bbox) : null);
  if (!geom) return null;
  return (
    <div className="mt-2 h-72 w-full max-w-[1100px] overflow-hidden rounded-md border border-border">
      <MapGL
        mapLib={maplibregl}
        initialViewState={bbox
          ? { bounds: [[bbox[0], bbox[1]], [bbox[2], bbox[3]]], fitBoundsOptions: { padding: 24 } }
          : { longitude: -111.7, latitude: 39.3, zoom: 5 }}
        mapStyle={POSITRON}
        interactive={false}
        attributionControl={false}
        style={{ width: "100%", height: "100%" }}
      >
        <Source id="fp-mini" type="geojson" data={{ type: "Feature", properties: {}, geometry: geom }}>
          <Layer id="fp-mini-line" type="line" paint={{ "line-color": "#888", "line-width": 1.5 }} />
        </Source>
      </MapGL>
    </div>
  );
}

// ---- asset viewer: peruse a publication's files in-page (PDF / image / COG / parquet / text) ----
type AssetKind = "cog" | "pdf" | "image" | "parquet" | "text" | "other";
const KIND_RANK: Record<AssetKind, number> = { cog: 0, pdf: 1, parquet: 2, image: 3, text: 4, other: 9 };
const KIND_LABEL: Record<AssetKind, string> = {
  cog: "Map", pdf: "PDF", parquet: "Data", image: "Image", text: "Text", other: "File",
};

const extOf = (href: string) => (href.split("?")[0].split(".").pop() ?? "").toLowerCase();
function assetKind(a: Asset): AssetKind {
  const t = (a.type ?? "").toLowerCase();
  const ext = extOf(a.href);
  if (t.includes("profile=cloud-optimized") || a.roles?.includes("cloud-optimized") || a.href.endsWith(".cog.tif")) return "cog";
  if (t === "application/pdf" || ext === "pdf") return "pdf";
  if (t.startsWith("image/") || ["png", "jpg", "jpeg", "gif", "webp", "svg"].includes(ext)) return "image";
  if (t.includes("parquet") || ext === "parquet") return "parquet";
  if (t.startsWith("text/") || ["csv", "txt", "tsv"].includes(ext)) return "text";
  return "other";
}

// Small text/CSV peek — fetch the head of the file and show it; no parsing, just a glance.
function TextPreview({ href }: { href: string }) {
  const [txt, setTxt] = useState<string>();
  const [err, setErr] = useState<string>();
  useEffect(() => {
    let live = true;
    fetch(href).then((r) => r.text())
      .then((t) => { if (live) setTxt(t.slice(0, 20000)); })
      .catch((e) => { if (live) setErr(e instanceof Error ? e.message : String(e)); });
    return () => { live = false; };
  }, [href]);
  if (err) return <div className="mt-2 text-xs text-destructive">preview failed: {err}</div>;
  if (txt === undefined) return <div className="mt-2 text-xs text-muted-foreground">loading…</div>;
  return (
    <pre className="mt-2 max-h-[600px] max-w-full overflow-auto rounded-md border border-border bg-muted p-3 text-[12px] leading-snug">
      {txt}{txt.length >= 20000 ? "\n… (truncated — open or download for the full file)" : ""}
    </pre>
  );
}

function AssetPane({ kind, asset, item }: { kind: AssetKind; asset: Asset; item: StacDoc }) {
  switch (kind) {
    case "cog": return <CogMap href={asset.href} item={item} />;
    case "parquet": return <DataExplorer href={asset.href} />;
    case "image":
      return (
        <img src={asset.href} alt={asset.title ?? "image"} loading="lazy"
          className="mt-2 max-h-[600px] w-auto max-w-full rounded-md border border-border bg-muted object-contain" />
      );
    case "pdf":
      return (
        <object data={asset.href} type="application/pdf"
          className="mt-2 h-[400px] sm:h-[640px] w-full max-w-[1100px] rounded-md border border-border">
          <div className="p-3 text-xs text-muted-foreground">
            Can’t embed this PDF — <a href={asset.href} target="_blank" rel="noopener" className="text-primary">open it ↗</a>
          </div>
        </object>
      );
    case "text": return <TextPreview href={asset.href} />;
    default:
      return (
        <div className="mt-2 rounded-md border border-border bg-muted p-3 text-xs text-muted-foreground">
          No in-page preview for this file type. <a href={asset.href} target="_blank" rel="noopener" className="text-primary">Download / open ↗</a>
        </div>
      );
  }
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
        <FootprintMini item={item} />
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

// Preview: vector serving topics → interactive PMTiles map + linked dataset explorer; everything
// else (publications) → the tabbed asset viewer so users can peruse every file in-page.
function Preview({ item }: { item: StacDoc }) {
  if (pmtilesLink(item)) return <VectorPreview item={item} />;
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
  return (
    <button
      onClick={() => { navigator.clipboard?.writeText(text); setDone(true); setTimeout(() => setDone(false), 1200); }}
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
  if (!links.length && !tables.length && !fks.length) return null;
  return (
    <section className="mt-4 max-w-[760px] rounded-md border border-border p-3">
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
              <li key={key} className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{asset.title ?? key}</span>
                <a href={asset.href} className="text-primary hover:underline" download>Parquet ↓</a>
                {asset.foreignKeys?.map((fk, i) => (
                  <span key={i} className="text-muted-foreground">(<code>{fk.fields.join(", ")}</code> → this)</span>
                ))}
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
      {typeof p.description === "string" && <p className="max-w-[760px] text-muted-foreground">{p.description}</p>}
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
      <ExportPanel item={item} />
      <EndpointsPanel item={item} />
      <RelatedPanel item={item} />
      <table className="mt-3 w-full max-w-[760px] table-fixed border-collapse text-sm">
        <tbody>
          {Object.entries(p)
            .filter(([k, v]) => v !== null && v !== "" && k !== "renders")
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
  series: string[];
  onSeries: (codes: string[]) => void;
  item?: StacDoc;
  itemSelected: boolean;
  onOpenCollection: (href: string) => void;
  onOpenItem: (href: string) => void;
  onBackToItems: () => void;
  onViewMap: () => void;
}) {
  const { collectionId, itemSelected, showItems, atRoot, search, onSearch, series, onSeries } = props;

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

  // browse level: root catalog (with search-all) OR a sub-catalog's series chooser
  return (
    <div className={C.wrap}>
      {!atRoot && <Breadcrumb crumbs={props.breadcrumb} />}
      {atRoot && (
        <div className={C.bar}>
          <input className={C.input} placeholder="Search all collections…" value={search}
            onChange={(e) => onSearch(e.target.value)} />
          {props.itemsLoading && <span className={C.muted}>loading items…</span>}
        </div>
      )}
      {atRoot && search.trim()
        ? <ItemList items={props.allItems} showCollection query={search} onOpen={props.onOpenItem} series={series} onSeries={onSeries} />
        : <Collections collections={props.cards} heading={atRoot ? "Collections" : "Series"} onOpen={props.onOpenCollection} />}
    </div>
  );
}
