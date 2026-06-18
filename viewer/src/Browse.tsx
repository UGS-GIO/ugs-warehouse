// Catalog-centric browser: metadata over map. Collection cards (with counts) +
// search-all → sortable item table / cards → item detail. The map is one link out.
import {
  type ColumnDef, flexRender, getCoreRowModel, getSortedRowModel,
  type SortingState, useReactTable,
} from "@tanstack/react-table";
import maplibregl from "maplibre-gl";
import { useEffect, useMemo, useRef, useState } from "react";
import { Layer, type LayerProps, Map as MapGL, type MapRef, NavigationControl, Source } from "react-map-gl/maplibre";
import { exportItem, type ExportFormat, FORMATS } from "./download";
import { Legend } from "./legend";
import { type Asset, citeLink, cogAsset, defaultStyleUrl, featuresCollectionUrl, type Link, pmtilesLink, type StacDoc, thumbnailAsset, viaLink } from "./stac";

export type CollectionSummary = {
  id: string; href: string; title?: string; description?: string; count: number; itemLinks: Link[];
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
  input: "w-72 rounded-md border border-input bg-card px-2.5 py-1.5 text-sm text-foreground placeholder:text-muted-foreground",
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
const gType = (it: ItemRef) => String(props(it)["ugs:pub_type"] ?? props(it)["ugs:series"] ?? props(it)["ugs:topic"] ?? "");
const gScale = (it: ItemRef) => String(props(it)["ugs:scale"] ?? "");
const haystack = (it: ItemRef) => (it.href + JSON.stringify(it.data?.properties ?? {})).toLowerCase();
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

function Collections({ collections, onOpen }: { collections: CollectionSummary[]; onOpen: (href: string) => void }) {
  if (!collections.length) return <p className={`${C.muted} mt-4`}>No collections in this catalog yet.</p>;
  return (
    <>
      <h2 className="mb-1 mt-1 text-lg font-semibold">Collections</h2>
      <div className={C.grid}>
        {collections.map((c) => {
          const desc = meaningfulDesc(c.description);
          return (
            <div key={c.href} className={C.card} onClick={() => onOpen(c.href)}>
              <p className="text-base font-semibold leading-tight">{c.title ?? humanize(c.id)}</p>
              <div className="mt-0.5 font-mono text-[11px] text-muted-foreground">{c.id}</div>
              {desc && <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">{desc}</p>}
              <span className={`${C.badge} mt-2`}>{c.count} item{c.count === 1 ? "" : "s"}</span>
            </div>
          );
        })}
      </div>
    </>
  );
}

// ---- item list: filter + sort + table/cards, reused for a collection and global search ----
function ItemList({ items, showCollection, query, onOpen, series, onSeries }: {
  items: ItemRef[]; showCollection?: boolean; query?: string; onOpen: (href: string) => void;
  series: string[]; onSeries: (codes: string[]) => void;
}) {
  const [q, setQ] = useState("");
  const [mode, setMode] = useState<"table" | "cards">("table");

  // Data-series facets — one chip per series code (DS, OFR, GQ…), with a count and the
  // human label. Multi-select: pick any combination; the selection lives in the URL
  // (?s=DS,OFR) so a "just the series I care about" view is shareable. Counts derive from
  // the full set so they stay stable as you toggle.
  const facets = useMemo(() => {
    const m = new Map<string, { n: number; label: string }>();
    for (const it of items) {
      const code = gCode(it);
      if (!code) continue;
      const cur = m.get(code) ?? { n: 0, label: gLabel(it) };
      m.set(code, { n: cur.n + 1, label: cur.label });
    }
    return [...m.entries()].sort((a, b) => b[1].n - a[1].n || a[0].localeCompare(b[0]));
  }, [items]);
  const sel = new Set(series);
  const toggleCode = (code: string) =>
    onSeries(sel.has(code) ? series.filter((c) => c !== code) : [...series, code]);

  const needle = (query ?? q).trim().toLowerCase();
  const rows = useMemo(
    () => items.filter((it) =>
      (!needle || haystack(it).includes(needle)) && (!sel.size || sel.has(gCode(it)))),
    [items, needle, series],
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
        <span className={toggle(mode === "table")} onClick={() => setMode("table")}>Table</span>
        <span className={toggle(mode === "cards")} onClick={() => setMode("cards")}>Cards</span>
      </div>

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

// cog:// protocol registered once, lazily — pulls geotiff.js only when a COG is first viewed.
let cogReady: Promise<void> | null = null;
const ensureCogProtocol = (): Promise<void> =>
  (cogReady ??= import("@geomatico/maplibre-cog-protocol").then(({ cogProtocol }) => {
    maplibregl.addProtocol("cog", cogProtocol);
  }));

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
function PmtilesMap({ item }: { item: StacDoc }) {
  const pm = pmtilesLink(item);
  const styleUrl = defaultStyleUrl(item);
  const [styleLayers, setStyleLayers] = useState<Record<string, unknown>[] | null>(null);
  useEffect(() => {
    if (!styleUrl) { setStyleLayers(null); return; }
    let live = true;
    fetch(styleUrl).then((r) => r.json())
      .then((d) => { if (live) setStyleLayers(Array.isArray(d?.layers) ? d.layers : null); })
      .catch(() => { if (live) setStyleLayers(null); });
    return () => { live = false; };
  }, [styleUrl]);
  if (!pm) return null;
  const sourceLayer = pm["pmtiles:layers"]?.[0] ?? String(item.id ?? "");
  const bounds = asBounds(item);
  return (
    <>
      <div className="mt-2 h-96 w-full max-w-[1100px] overflow-hidden rounded-md border border-border bg-muted">
        <MapGL
          mapLib={maplibregl}
          initialViewState={bounds ? { bounds, fitBoundsOptions: { padding: 16 } } : { longitude: -111.7, latitude: 39.3, zoom: 6 }}
          mapStyle={POSITRON}
          style={{ width: "100%", height: "100%" }}
        >
          <NavigationControl position="top-right" showCompass={false} />
          <Source id="pm-prev" type="vector" url={`pmtiles://${pm.href}`} />
          {(styleLayers ?? NEUTRAL_LAYERS).map((l, i) => (
            // explicit `source` (+ source-layer) on each Layer — react-map-gl won't inject it into
            // an array / Fragment, so without this they render with no source (invisible).
            <Layer key={i} {...({ ...l, id: `pm-prev-${i}`, source: "pm-prev", "source-layer": sourceLayer } as unknown as LayerProps)} />
          ))}
        </MapGL>
      </div>
      {styleLayers && <Legend layers={styleLayers} />}
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

// Canonical datalake glance — first rows of the GeoParquet via DuckDB-WASM (TanStack Table, sortable).
function ParquetTable({ href }: { href: string }) {
  const [data, setData] = useState<{ columns: string[]; rows: Record<string, unknown>[] } | null>(null);
  const [err, setErr] = useState<string>();
  useEffect(() => {
    let live = true;
    import("./download").then(({ previewRows }) => previewRows(href, 12))
      .then((d) => { if (live) setData(d); })
      .catch((e) => { if (live) setErr(e instanceof Error ? e.message : String(e)); });
    return () => { live = false; };
  }, [href]);
  const MAX = 12;
  const columns = useMemo<ColumnDef<Record<string, unknown>, unknown>[]>(
    () => (data?.columns ?? []).slice(0, MAX).map((c) => ({
      id: c, header: c, accessorFn: (row) => row[c], sortingFn: "alphanumeric",
      cell: (info) => {
        const v = info.getValue();
        const s = v == null ? "" : String(v);
        return <span className="block max-w-[240px] truncate" title={s}>{s}</span>;
      },
    })),
    [data],
  );
  if (err) return <div className="mt-2 text-xs text-destructive">data preview failed: {err}</div>;
  if (!data) return <div className="mt-2 text-xs text-muted-foreground">loading data…</div>;
  if (!data.rows.length) return null;
  const more = data.columns.length - Math.min(MAX, data.columns.length);
  return (
    <div className="mt-2 max-w-full overflow-x-auto rounded-md border border-border text-[12px]">
      <DataTable columns={columns} data={data.rows} />
      {more > 0 && (
        <div className="px-2.5 py-1.5 text-[11px] text-muted-foreground">
          +{more} more columns — full data via Download or the OGC API.
        </div>
      )}
    </div>
  );
}

// Preview, best-first by data type: interactive COG (raster) > interactive PMTiles map +
// GeoParquet data table (vector) > thumbnail image > non-interactive footprint locator >
// nothing. The full multi-layer map stays behind "View on map".
function Preview({ item }: { item: StacDoc }) {
  const cog = cogAsset(item);
  if (cog) return <CogMap href={cog.href} item={item} />;

  if (pmtilesLink(item)) {
    const pq = parquetAsset(item);
    return (
      <>
        <PmtilesMap item={item} />
        {pq && <ParquetTable href={pq.href} />}
      </>
    );
  }

  const thumb = thumbnailAsset(item);
  if (thumb) {
    return (
      <img src={thumb.href} alt={thumb.title ?? "preview"} loading="lazy"
        className="mt-2 max-h-60 w-auto rounded-md border border-border bg-muted object-contain" />
    );
  }
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

// Property key/value formatting for the detail table — drop the `ugs:` prefix, underscores →
// spaces; join arrays, stringify objects, so nothing renders as `[object Object]` or overflows.
const prettyKey = (k: string) => k.replace(/^ugs:/, "").replace(/_/g, " ");
const fmtVal = (v: unknown): string =>
  Array.isArray(v) ? v.join(", ") : v && typeof v === "object" ? JSON.stringify(v) : String(v);

// ---- item detail ----
function ItemDetail({ collectionId, item, onBack, onMap }: {
  collectionId: string; item?: StacDoc; onBack: () => void; onMap: () => void;
}) {
  if (!item) return <em className={C.muted}>Loading…</em>;
  const p = item.properties ?? {};
  const hasGeom = Boolean(item.geometry || item.bbox);
  const via = viaLink(item);
  const cite = citeLink(item);
  // OGC API Features link — only for vector layers (a featureserv collection == this item id).
  const ogc = (pmtilesLink(item) || parquetAsset(item))
    ? featuresCollectionUrl(String(item.id ?? "")) : undefined;
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
        {ogc && (
          <a href={ogc} target="_blank" rel="noopener"
            className="inline-block rounded border border-border px-2.5 py-1 text-[11px] text-foreground no-underline hover:border-primary">
            OGC API ↗
          </a>
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
  collections: CollectionSummary[];
  collectionId?: string;
  allItems: ItemRef[];
  itemsLoading: boolean;
  search: string;
  onSearch: (q: string) => void;
  series: string[];
  onSeries: (codes: string[]) => void;
  item?: StacDoc;
  itemSelected: boolean;
  onOpenCollection: (href: string) => void;
  onOpenItem: (href: string) => void;
  onBackToCollections: () => void;
  onBackToItems: () => void;
  onViewMap: () => void;
}) {
  const { collectionId, itemSelected, search, onSearch, series, onSeries } = props;

  // item detail
  if (collectionId && itemSelected) {
    return (
      <div className={C.wrap}>
        <ItemDetail collectionId={collectionId} item={props.item}
          onBack={props.onBackToItems} onMap={props.onViewMap} />
      </div>
    );
  }

  // a collection's items
  if (collectionId) {
    const items = props.allItems.filter((it) => it.collId === collectionId);
    return (
      <div className={C.wrap}>
        <div className="mb-1">
          <span className={C.crumb} onClick={props.onBackToCollections}>Collections</span>
          <span className={C.muted}> / {collectionId}</span>
          {props.itemsLoading && <span className={C.muted}> · loading…</span>}
        </div>
        <ItemList items={items} onOpen={props.onOpenItem} series={series} onSeries={onSeries} />
      </div>
    );
  }

  // catalog root: search-all OR collection cards
  return (
    <div className={C.wrap}>
      <div className={C.bar}>
        <input className={C.input} placeholder="Search all collections…" value={search}
          onChange={(e) => onSearch(e.target.value)} />
        {props.itemsLoading && <span className={C.muted}>loading items…</span>}
      </div>
      {search.trim()
        ? <ItemList items={props.allItems} showCollection query={search} onOpen={props.onOpenItem} series={series} onSeries={onSeries} />
        : <Collections collections={props.collections} onOpen={props.onOpenCollection} />}
    </div>
  );
}
