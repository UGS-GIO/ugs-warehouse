// Catalog-centric browser: metadata over map. Collection cards (with counts) +
// search-all → sortable item table / cards → item detail. The map is one link out.
import {
  type ColumnDef, getCoreRowModel, getPaginationRowModel, getSortedRowModel,
  type SortingState, useReactTable, type VisibilityState,
} from "@tanstack/react-table";
import { useMemo, useState } from "react";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AssetChips } from "./asset-viewer";
import { rootGroupOf } from "./catalog";
import { createComment } from "./comments";
import { ItemDetail } from "./item-detail";
import { author, collectionLabel, county, dateOf, fmtDate, scale, series, title, typeOf, year } from "./item-view";
import { PageHero } from "./page-hero";
import { T } from "./page";
import { ALL_PAGES, DEFAULT_PAGE_SIZE, PAGE_SIZES, type PageSize } from "./paging";
import { type Asset, assetKind, cogAsset, IS_REVIEW, pmtilesLink, rasterTilesAsset, type StacDoc, thumbnailAsset, zarrAsset } from "./stac";
import { DataTable, Pager } from "./table";
import { useIsDesktop } from "./ui/use-breakpoint";
import { C, humanize, toggle } from "./ui";

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

const ROOT_GROUPS = [
  { group: "layers", heading: "Map layers" },
  { group: "documents", heading: "Publications & records" },
  { group: "federated", heading: "Other UGS catalogs" },
  { group: "other", heading: "Everything else" },
] as const;



const BADGE_KEYS = ["ugs:series", "ugs:pub_type", "ugs:topic", "ugs:scale", "ugs:author"];


const props = (it: ItemRef) => it.data?.properties ?? {};
// Survey Notes volume (warehouse ugs:volume, from SNT-{vol}-{issue}) → group issues under it.
const gVol = (it: ItemRef): number | null => {
  const v = props(it)["ugs:volume"];
  return typeof v === "number" ? v : null;
};
// The generic item getters live in item-view.ts (the one source of truth, shared with the discovery
// core). Thin local aliases keep the rest of this module reading gTitle/gSeries/gType/… unchanged.
const gSeries = series;
const gColl = (it: ItemRef) => collectionLabel(it.collId);
const gTitle = title;
const gDate = dateOf;
const gYear = year;
const gType = typeOf;
const gScale = scale;
const gAuthor = author;
const gCounty = county;
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
// "Mappable" = has something to draw on the map: a COG (raster), vector PMTiles, a raster PMTiles
// mosaic, or a zarr datacube. Items with none (metadata-only pubs) do nothing when toggled — the
// filter hides them.
const hasMapData = (it: ItemRef) =>
  !!(cogAsset(it.data) || pmtilesLink(it.data) || rasterTilesAsset(it.data) || zarrAsset(it.data));
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




// Client-side export — only for items with a GeoParquet asset (serving topics).

// ---- collection cards ----
// The warehouse emits a placeholder "UGS warehouse — {id}." description; hide it as noise.
const meaningfulDesc = (d?: string) => (d && !/^UGS warehouse — .*\.$/.test(d) ? d : null);

function Collections({ collections, heading, onOpen, onOpenItem }: {
  collections: CollectionSummary[]; heading: string; onOpen: (href: string) => void;
  onOpenItem: (href: string) => void;
}) {
  if (!collections.length) return <p className={`${C.muted} mt-4`}>Nothing here yet.</p>;
  return (
    <>
      <h2 className={`mb-2 mt-1 ${T.section}`}>{heading}</h2>
      <div className={C.grid}>
        {collections.map((c) => {
          const desc = meaningfulDesc(c.description);
          return (
            <div key={c.href} className={C.card} onClick={() => onOpen(c.href)}>
              <div className="flex items-baseline justify-between gap-3">
                <p className={T.cardTitle}>{c.title ?? humanize(c.id)}</p>
                {c.count != null && (
                  <span className="shrink-0 text-sm font-medium tabular-nums text-muted-foreground">
                    {c.count.toLocaleString()}
                  </span>
                )}
              </div>
              <div className="mt-0.5 font-mono text-xs text-muted-foreground">{c.id}</div>
              {desc && <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">{desc}</p>}
              {c.covers && c.covers.length > 0 && (
                <div className="mt-2.5 grid grid-cols-4 gap-1.5" title="Latest covers — click to open">
                  {c.covers.slice(0, 4).map((cv) => (
                    <img key={cv.href} src={cv.thumb} alt={cv.title ?? ""} loading="lazy" title={cv.title ?? ""}
                      onClick={(e) => { e.stopPropagation(); onOpenItem(cv.href); }}
                      className="aspect-[3/4] w-full cursor-pointer rounded-sm border border-border bg-muted object-cover hover:border-primary" />
                  ))}
                </div>
              )}
              <div className="mt-auto flex flex-wrap items-center gap-1.5 pt-2.5">
                {c.kind === "catalog" && <span className={C.badge}>by series</span>}
                {c.mappable === 0
                  ? <span className={C.badge}>no map data</span>
                  : c.mappable != null && c.count != null && c.mappable < c.count
                    ? <span className={C.badge}>{c.mappable.toLocaleString()} on map</span>
                    : null}
                {/* Each child IS a complete STAC catalog — hand out its URL so a client (QGIS,
                    pystac, a harvester) can crawl just this part without the rest. */}
                <a href={c.href} target="_blank" rel="noopener" title={c.href}
                  onClick={(e) => e.stopPropagation()}
                  className="ml-auto text-xs text-primary no-underline hover:underline">STAC ↗</a>
              </div>
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
        <div className="font-mono text-xs font-semibold text-foreground">{gSeries(it)}</div>
        <p className="line-clamp-2 text-xs text-muted-foreground">{gTitle(it)}</p>
      </div>
    </div>
  );
}
// A card in the Cards view (text-forward; same data as the table row).
function CardItem({ it, showCollection, onOpen }: { it: ItemRef; showCollection?: boolean; onOpen: (href: string) => void }) {
  return (
    <div className={C.card} onClick={() => onOpen(it.href)}>
      <div className="font-mono text-xs font-semibold text-foreground">{gSeries(it)}</div>
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
  // "All items" is ~7.5k rows; pages by default, "All" still offered for ctrl-F.
  const [pageSize, setPageSize] = useState<PageSize>(DEFAULT_PAGE_SIZE);
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

  const desktop = useIsDesktop();
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
      cell: (i) => <span className="break-all font-mono text-sm font-semibold text-foreground md:whitespace-nowrap md:break-normal">{String(i.getValue())}</span> },
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
  const [sorting, setSorting] = useState<SortingState>(defaultSorting);
  const [pageIndex, setPageIndex] = useState(0);
  // "All" tracks the row count so clearing a filter widens the page with it.
  const perPage = pageSize === ALL_PAGES ? Math.max(rows.length, 1) : pageSize;

  // One instance for all three view modes: it sorts, then pages, and `autoResetPageIndex`
  // returns you to page 1 whenever a filter or the sort changes the row set.
  // A phone fits ID + Title + Date. The rest — and especially the asset chips, which stack one per
  // line in a narrow cell and blow rows out to ~280px — are desktop-only; Cards view carries them.
  const columnVisibility = useMemo<VisibilityState>(
    () => (desktop
      ? {}
      : Object.fromEntries(["collection", "volume", "type", "scale", "assets"].map((c) => [c, false]))),
    [desktop]);

  const table = useReactTable({
    data: rows, columns,
    state: { sorting, columnVisibility, pagination: { pageIndex, pageSize: perPage } },
    onSortingChange: setSorting,
    onPaginationChange: (u) => setPageIndex((prev) =>
      (typeof u === "function" ? u({ pageIndex: prev, pageSize: perPage }) : u).pageIndex),
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
  });
  const pageRows = table.getRowModel().rows.map((r) => r.original);

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
        <span className={toggle(mapOnly)} title="Only items with a COG, vector tiles, or a zarr datacube to display on the map"
          onClick={() => setMapOnly((v) => !v)}>Mappable</span>
        <span className={toggle(mode === "table")} onClick={() => setMode("table")}>Table</span>
        <span className={toggle(mode === "thumbs")} onClick={() => setMode("thumbs")}>Thumbnails</span>
        <span className={toggle(mode === "cards")} onClick={() => setMode("cards")}>Cards</span>
      </div>

      {topicFacets.length > 1 && (
        <div className="mb-2 flex flex-wrap items-center gap-1.5">
          <span className="mr-0.5 text-xs uppercase tracking-wide text-muted-foreground">Topic</span>
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
          <span className="mr-0.5 text-xs uppercase tracking-wide text-muted-foreground">County</span>
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
          <span className="mr-0.5 text-xs uppercase tracking-wide text-muted-foreground">Scale</span>
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
          <span className="mr-0.5 text-xs uppercase tracking-wide text-muted-foreground">Series</span>
          {facets.map(([code, { n, label }]) => (
            <span key={code} className={toggle(sel.has(code))} title={label}
              onClick={() => toggleCode(code)}>{code} · {n}</span>
          ))}
          {sel.size > 0 && (
            <span className="cursor-pointer text-xs text-primary" onClick={() => onSeries([])}>clear</span>
          )}
        </div>
      )}

      {/* Top and bottom: bottom is where you land after scrolling, top is how you change size
          without scrolling back down. */}
      {rows.length > PAGE_SIZES[0] && (
        <Pager table={table} size={pageSize}
          onSize={(s) => { setPageSize(s); setPageIndex(0); }} />
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
            <DataTable table={table} onRowClick={(it) => onOpen(it.href)} />
          </div>
        </>
      ) : mode === "thumbs" ? (
        <VolumeGrouped rows={pageRows} gridClass={THUMB_GRID}
          render={(it) => <ThumbCard key={it.href} it={it} onOpen={onOpen} />} />
      ) : (
        <VolumeGrouped rows={pageRows} gridClass={C.grid}
          render={(it) => <CardItem key={it.href} it={it} showCollection={showCollection} onOpen={onOpen} />} />
      )}

      {rows.length > PAGE_SIZES[0] && (
        <Pager table={table} size={pageSize}
          onSize={(s) => { setPageSize(s); setPageIndex(0); }} />
      )}
    </>
  );
}

// Schema panel from the STAC Table extension (`table:columns`). The dataset's fields + types
// straight from the catalog — no parquet read. Hidden pre-reingest (extension not emitted yet).


// All the ways out to the data: the OGC API Features service (= the modern WFS — REST + GeoJSON,
// served by featureserv), plus the raw artifacts (GeoParquet, PMTiles, DuckLake). Replaces the
// lone "OGC API" link that dumped users on a featureserv page with no hint of the other endpoints.

// ---- item detail ----
// A related item's STAC .json href → a viewer deep-link (?c=<collection>&i=<id>).

// Registry-driven relationships (FK graph): related layers, this layer's references, related tables.
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
        <ItemDetail collectionId={collectionId} item={props.item} layout="page"
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
    <>
      {/* The catalog landing gets the same title band as the content pages — it IS the front door,
          and the search that opens the whole catalog belongs in it rather than above a bare list. */}
      {atRoot && (
        <PageHero title="Data Catalog"
          lead="Geologic maps, hazard layers and publications." />
      )}
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
        : atRoot
          // Two kinds of thing live at the root: layers you add to a map, and documents you read.
          ? ROOT_GROUPS.map(({ group, heading }) => {
              const cards = props.cards.filter((c) => rootGroupOf(c.id) === group);
              return cards.length
                ? <Collections key={group} collections={cards} heading={heading}
                    onOpen={props.onOpenCollection} onOpenItem={props.onOpenCover} />
                : null;
            })
          : <Collections collections={props.cards} heading="Series" onOpen={props.onOpenCollection} onOpenItem={props.onOpenCover} />}
    </div>
    </>
  );
}
