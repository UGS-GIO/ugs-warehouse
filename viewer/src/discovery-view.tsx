// The full-width Discover view: a GDR-style split of a LEFT facet rail · CENTER result cards (the
// star) · RIGHT co-equal map, over every loaded catalog item. It reuses the viewer's own plumbing —
// the shared MiniSearch index (search-index), the pure facet/filter/sort core (discovery-model), the
// map ItemMap (footprints + hover/bounds sync), and the existing ItemDetail for the selection drawer
// — restyled onto the Utah Design System tokens + shared controls (UiSegmented / UiSelect), no new deps.
// All pure logic lives in ./discovery-model; this file is the React shell + the map/detail wiring.
import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";

import type { ItemRef } from "./browse";
import {
  applyFacets, collectionLabel, discoverySeries, discoveryTitle, docIdOf, extractFacets,
  type FacetCount, type FacetSelection, filterByViewport, hasGeometry, sortItems, type SortKey,
  SORTS, typeOf,
} from "./discovery-model";
import type { Footprint } from "./map-model";
import { buildIndex, toSearchDoc } from "./search-index";
import { type StacDoc, thumbnailAsset } from "./stac";
import { ItemDetail } from "./item-detail";
import { C } from "./ui";
import { UiSegmented } from "./ui/segmented";
import { UiSelect } from "./ui/select";
import { useIsWide } from "./ui/use-breakpoint";

// maplibre is ~1.5MB — lazy so the rail + cards paint immediately and the map streams in behind them
// (App already code-splits ./map, so this shares that chunk).
const ItemMap = lazy(() => import("./map").then((m) => ({ default: m.ItemMap })));

type Layout = "gallery" | "list";
type Density = "comfortable" | "compact";
const LAYOUTS = [{ value: "gallery" as const, label: "Gallery" }, { value: "list" as const, label: "List" }];
const DENSITIES = [{ value: "comfortable" as const, label: "Comfy" }, { value: "compact" as const, label: "Compact" }];
const SORT_ITEMS = SORTS.map((s) => ({ value: s.key, label: s.label }));
const PAGE = 48; // cards per "Show more" step (reference parity)

const idOf = (href: string) => href.split("/").slice(-2)[0];
const dateOf = (it: ItemRef): string => {
  const d = (it.data?.properties as Record<string, unknown> | undefined)?.datetime;
  return typeof d === "string" ? d.slice(0, 10) : "";
};
// Escape a value for a [data-href="…"] selector (scroll a map-hovered card into view).
const escAttr = (s: string) => s.replace(/["\\]/g, "\\$&");
// A shareable viewer URL that opens the item in Discover — the fallback a cmd/middle-click uses to
// open a new tab. A plain left-click is intercepted (→ in-app nav) so it never navigates here itself.
const openHref = (it: ItemRef) => `?view=discover&c=${encodeURIComponent(it.collId)}&i=${encodeURIComponent(idOf(it.href))}`;

type LinkAttrs = {
  href: string; "data-href": string;
  onClick: (e: React.MouseEvent) => void;
  onMouseEnter: () => void; onMouseLeave: () => void;
};

export function DiscoveryView({
  items, itemsKey, onOpenItem, itemSelected, selectedItem, selectedCollectionId, onCloseItem, onViewOnMap,
}: {
  items: ItemRef[];
  itemsKey: string; // stable identity for the (deliberately unmemoized) items array — App's mapLoadKey
  onOpenItem: (href: string) => void;
  itemSelected: boolean;               // an item is selected (?i=) → show the detail drawer
  selectedItem?: StacDoc;              // its full doc (App resolves it from ?c=/?i=); undefined while loading
  selectedCollectionId?: string;
  onCloseItem: () => void;             // clears ?i=
  onViewOnMap: () => void;             // opens the selected item on the Map view
}) {
  const [q, setQ] = useState("");
  const [colls, setColls] = useState<string[]>([]);
  const [types, setTypes] = useState<string[]>([]);
  const [geometry, setGeometry] = useState<FacetSelection["geometry"]>("all");
  const [sort, setSort] = useState<SortKey>("relevance");
  const [layout, setLayout] = useState<Layout>("gallery");
  const [density, setDensity] = useState<Density>("comfortable");
  const [showMap, setShowMap] = useState(true);
  const [hoverHref, setHoverHref] = useState<string | null>(null);
  const [bounds, setBounds] = useState<[number, number, number, number] | null>(null); // live viewport
  const [area, setArea] = useState<[number, number, number, number] | null>(null);      // applied "this area"
  const [visible, setVisible] = useState(PAGE);
  const isWide = useIsWide(); // only MOUNT the map pane at ≥lg — keeps maplibre off phones/tablets

  // Heavy bits memoized on the stable items key (App's mapLoadKey), not the array identity — the
  // items array is rebuilt every render, so rebuilding the index each keystroke would re-index
  // thousands of docs. Matches App's own mapLoadKey memo pattern.
  const withData = useMemo(() => items.filter((it) => it.data), [itemsKey]); // eslint-disable-line react-hooks/exhaustive-deps
  const index = useMemo(
    () => buildIndex([], withData.map((it) => toSearchDoc(it.collId, it.data!))).index,
    [itemsKey], // eslint-disable-line react-hooks/exhaustive-deps
  );
  // href → bbox for O(1) highlight lookup on hover (rather than scanning withData each hover render).
  const bboxByHref = useMemo(() => {
    const m = new Map<string, number[] | undefined>();
    for (const it of withData) m.set(it.href, it.data?.bbox);
    return m;
  }, [itemsKey]); // eslint-disable-line react-hooks/exhaustive-deps

  // Text narrows first (score-ordered via the shared index); facets/area/sort are pure and cheap.
  const queried = useMemo(() => {
    const query = q.trim();
    if (query.length < 2) return withData;
    const order = new Map((index.search(query) as unknown as { id: string }[]).map((h, i) => [h.id, i]));
    return withData
      .filter((it) => order.has(docIdOf(it)))
      .sort((a, b) => (order.get(docIdOf(a)) ?? 0) - (order.get(docIdOf(b)) ?? 0));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [itemsKey, q, index]);

  // Facet counts over the search-narrowed set: they respond to the query (the primary narrowing) but
  // stay stable as you toggle facets — the rail reads as a table of contents, not a jumping wall.
  const facets = useMemo(() => extractFacets(queried), [queried]);

  const results = useMemo(() => {
    let base = applyFacets(queried, { collections: colls, types, geometry });
    if (area) base = filterByViewport(base, area);
    return sortItems(base, sort);
  }, [queried, colls, types, geometry, area, sort]);

  // Reset paging whenever the working set changes (new query/filter/sort) — reference parity.
  useEffect(() => setVisible(PAGE), [q, colls, types, geometry, area, sort, itemsKey]);
  const shown = results.slice(0, visible);

  // Every result's footprint → the map's coverage overlay (synced to the card set as filters narrow).
  const footprints = useMemo<Footprint[]>(() => results
    .map((it) => ({ href: it.href, id: idOf(it.href), title: discoveryTitle(it), bbox: it.data?.bbox }))
    .filter((f): f is Footprint => Array.isArray(f.bbox) && f.bbox.length >= 4), [results]);
  const hoverBbox = hoverHref ? bboxByHref.get(hoverHref) : undefined;

  // Hover sync. Track WHERE the hover came from: only a MAP-originated hover scrolls the card list —
  // a card-originated hover must not scroll, or revealing a clipped card shifts content under the
  // cursor and jitters between neighbors.
  const hoverSrc = useRef<"map" | "card" | null>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const onMapHover = (href: string | null) => { hoverSrc.current = "map"; setHoverHref(href); };
  useEffect(() => {
    if (!hoverHref || hoverSrc.current !== "map") return;
    const el = listRef.current?.querySelector(`[data-href="${escAttr(hoverHref)}"]`);
    (el as HTMLElement | null)?.scrollIntoView({ block: "nearest" });
  }, [hoverHref]);

  // Result link: a real <a> (keyboard-focusable + cmd/middle-click opens a new tab), but a plain
  // left-click is intercepted for in-app nav — the same pattern the app's home link uses.
  const cardLink = (it: ItemRef): LinkAttrs => ({
    href: openHref(it),
    "data-href": it.href,
    onClick: (e) => {
      if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return;
      e.preventDefault();
      onOpenItem(it.href);
    },
    onMouseEnter: () => { hoverSrc.current = "card"; setHoverHref(it.href); },
    onMouseLeave: () => { hoverSrc.current = "card"; setHoverHref(null); },
  });

  // Detail-drawer a11y: focus the close button on open, restore focus on close, Escape closes. Keyed
  // on itemSelected only; onCloseItem rides a ref so its new identity each render doesn't re-run this.
  const closeRef = useRef<HTMLButtonElement>(null);
  const restoreFocus = useRef<HTMLElement | null>(null);
  const closeCb = useRef(onCloseItem);
  closeCb.current = onCloseItem;
  useEffect(() => {
    if (!itemSelected) return;
    restoreFocus.current = document.activeElement as HTMLElement | null;
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") closeCb.current(); };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      restoreFocus.current?.focus();
    };
  }, [itemSelected]);

  const toggleIn = (list: string[], set: (v: string[]) => void, key: string) =>
    set(list.includes(key) ? list.filter((k) => k !== key) : [...list, key]);
  const activeFilters = colls.length + types.length + (geometry === "all" ? 0 : 1) + (area ? 1 : 0);
  const resetAll = () => { setColls([]); setTypes([]); setGeometry("all"); setArea(null); };

  return (
    <div className="relative flex h-full min-h-0 flex-col overflow-hidden bg-background text-foreground">
      {/* ── Top bar: search · count · (map-area) · sort · density · layout · map toggle ────────── */}
      <div className="flex flex-wrap items-center gap-2 border-b border-border bg-background px-3 py-2">
        <input value={q} onChange={(e) => setQ(e.target.value)}
          placeholder="Search every layer & publication…" aria-label="Search the catalog"
          className="min-w-[12rem] flex-1 rounded-md border border-input bg-card px-3 py-1.5 text-sm text-foreground outline-none placeholder:text-muted-foreground focus:border-primary sm:max-w-md" />
        <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
          <b className="text-foreground">{results.length}</b> of {withData.length}
        </span>
        {/* Clearable even when the map is hidden — the map's own "clear area" pill can be off-screen. */}
        {area && (
          <button type="button" onClick={() => setArea(null)}
            title="Results are limited to the map area — click to clear"
            className="shrink-0 rounded-full border border-primary bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary hover:bg-primary/20">
            Map area ✕
          </button>
        )}
        <div className="ml-auto flex flex-wrap items-center gap-1.5">
          <label className="flex items-center gap-1 text-xs text-muted-foreground">
            Sort
            <UiSelect value={sort} onValueChange={setSort} items={SORT_ITEMS} className="text-xs" />
          </label>
          <UiSegmented value={density} onValueChange={setDensity} items={DENSITIES} className="text-xs" />
          <UiSegmented value={layout} onValueChange={setLayout} items={LAYOUTS} className="text-xs" />
          {isWide && (
            <button type="button" onClick={() => setShowMap((v) => !v)} aria-pressed={showMap}
              title={showMap ? "Hide the map" : "Show the map"}
              className={`shrink-0 rounded-md border px-2.5 py-1 text-sm ${showMap
                ? "border-primary bg-primary text-primary-foreground"
                : "border-input bg-card text-foreground hover:bg-muted"}`}>
              Map
            </button>
          )}
        </div>
      </div>

      {/* ── Main split: facet rail · results · map ────────────────────────────────────────────── */}
      <div className="flex min-h-0 flex-1">
        {/* LEFT — facet rail (grouped, sectioned, collapsible, with counts). */}
        <aside className="hidden w-[264px] shrink-0 flex-col overflow-y-auto border-r border-border bg-background md:flex">
          <div className="flex items-center justify-between px-3 pb-1 pt-3">
            <h2 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Filters</h2>
            {activeFilters > 0 && (
              <button type="button" onClick={resetAll} className="text-xs text-primary hover:underline">Clear all</button>
            )}
          </div>
          <FacetSection label="Collection" facets={facets.collections} selected={new Set(colls)}
            onToggle={(k) => toggleIn(colls, setColls, k)} />
          <FacetSection label="Type" facets={facets.types} selected={new Set(types)}
            onToggle={(k) => toggleIn(types, setTypes, k)} />
          <GeometrySection facets={facets.geometry} value={geometry} onChange={setGeometry} />
          <div className="px-3 py-4 text-[11px] leading-snug text-muted-foreground">
            Filters narrow the cards and the map together. Hover a card to find it on the map.
          </div>
        </aside>

        {/* CENTER — result cards (the star): gallery grid or list, paginated. */}
        <div ref={listRef} className="min-h-0 flex-1 overflow-y-auto bg-muted/30 px-3 py-3">
          {withData.length === 0 ? (
            <p className="px-1 py-16 text-center text-sm text-muted-foreground">Loading the catalog…</p>
          ) : shown.length === 0 ? (
            <div className="mx-auto mt-10 max-w-sm rounded-lg border border-dashed border-border p-8 text-center">
              <p className="text-sm font-medium text-foreground">Nothing matches these filters.</p>
              {activeFilters > 0 && (
                <button type="button" onClick={resetAll} className="mt-2 text-xs text-primary hover:underline">Clear all filters</button>
              )}
            </div>
          ) : layout === "gallery" ? (
            <div className="grid gap-3 [grid-template-columns:repeat(auto-fill,minmax(240px,1fr))]">
              {shown.map((it) => (
                <ResultCard key={it.href} it={it} density={density} on={hoverHref === it.href} link={cardLink(it)} />
              ))}
            </div>
          ) : (
            <ul className="divide-y divide-border overflow-hidden rounded-lg border border-border bg-card">
              {shown.map((it) => (
                <ResultRow key={it.href} it={it} density={density} on={hoverHref === it.href} link={cardLink(it)} />
              ))}
            </ul>
          )}
          {results.length > shown.length && (
            <div className="mt-3 flex justify-center">
              <button type="button" onClick={() => setVisible((v) => v + PAGE)}
                className="rounded-md border border-border bg-card px-4 py-1.5 text-sm text-foreground hover:border-primary">
                Show {Math.min(PAGE, results.length - shown.length)} more
                <span className="ml-1 text-xs text-muted-foreground">({results.length - shown.length} of {results.length} remaining)</span>
              </button>
            </div>
          )}
        </div>

        {/* RIGHT — co-equal synced map (~40%). MOUNTED only at ≥lg. Footprints track the result set. */}
        {isWide && showMap && (
          <div className="relative min-h-0 w-2/5 shrink-0 border-l border-border">
            <Suspense fallback={<div className="grid h-full place-items-center bg-muted text-sm text-muted-foreground">Loading map…</div>}>
              <ItemMap layers={[]} footprints={footprints} onPickFootprint={onOpenItem}
                highlightBbox={hoverBbox} onHoverFootprint={onMapHover} onBoundsChange={setBounds}
                coverageDefault />
            </Suspense>
            <div className="pointer-events-none absolute inset-x-0 top-3 z-10 flex justify-center">
              {area ? (
                <button type="button" onClick={() => setArea(null)}
                  className="pointer-events-auto rounded-full border border-primary bg-primary px-3 py-1 text-xs font-medium text-primary-foreground shadow">
                  ✕ Clear map area
                </button>
              ) : (
                <button type="button" onClick={() => bounds && setArea(bounds)} disabled={!bounds}
                  title="Limit results to what's in the current map view"
                  className="pointer-events-auto rounded-full border border-border bg-card/95 px-3 py-1 text-xs font-medium text-foreground shadow hover:bg-muted disabled:opacity-50">
                  Search this area
                </button>
              )}
            </div>
          </div>
        )}
      </div>

      {/* ── Detail drawer: the selected item, in place (reuses the catalog ItemDetail). ─────────── */}
      {itemSelected && (
        <>
          <div className="absolute inset-0 z-20 bg-black/40" onClick={onCloseItem} aria-hidden />
          <aside role="dialog" aria-modal="true" aria-label="Item detail"
            className="absolute inset-y-0 right-0 z-30 flex w-full max-w-[560px] flex-col border-l border-border bg-background shadow-xl">
            <div className="flex shrink-0 items-center justify-between border-b border-border px-4 py-2">
              <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Item detail</span>
              <button ref={closeRef} type="button" onClick={onCloseItem}
                className="rounded px-2 py-1 text-sm text-muted-foreground hover:bg-muted">✕ Close</button>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
              <ItemDetail collectionId={selectedCollectionId ?? ""} item={selectedItem}
                onBack={onCloseItem} onMap={onViewOnMap} />
            </div>
          </aside>
        </>
      )}
    </div>
  );
}

// ── Facet rail section: a collapsible group of checkbox rows with counts. A lone value isn't a
// filter, so a group under two options hides (same rule the model uses). Long groups collapse to a
// "Show all" so the rail never becomes a wall. ─────────────────────────────────────────────────────
const FACET_HEAD = 8;
function FacetSection({ label, facets, selected, onToggle }: {
  label: string; facets: FacetCount[]; selected: Set<string>; onToggle: (key: string) => void;
}) {
  const [open, setOpen] = useState(true);
  const [all, setAll] = useState(false);
  if (facets.length < 2) return null;
  const rows = all ? facets : facets.slice(0, FACET_HEAD);
  return (
    <section className="border-t border-border px-2 py-2">
      <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open}
        className="flex w-full items-center justify-between px-1 py-0.5 text-left">
        <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{label}</span>
        <span aria-hidden className="text-xs text-muted-foreground">{open ? "▾" : "▸"}</span>
      </button>
      {open && (
        <ul className="mt-1 space-y-0.5">
          {rows.map((f) => {
            const on = selected.has(f.key);
            return (
              <li key={f.key}>
                <button type="button" onClick={() => onToggle(f.key)} title={f.key} aria-pressed={on}
                  className={`flex w-full items-center gap-2 rounded px-2 py-1 text-left text-sm ${on
                    ? "bg-muted font-medium text-foreground" : "text-foreground hover:bg-muted"}`}>
                  <span aria-hidden className={`grid h-4 w-4 shrink-0 place-items-center rounded border text-[10px] leading-none ${on
                    ? "border-primary bg-primary text-primary-foreground" : "border-border bg-card"}`}>{on ? "✓" : ""}</span>
                  <span className="min-w-0 flex-1 truncate" title={f.label}>{f.label}</span>
                  <span className="shrink-0 tabular-nums text-xs text-muted-foreground">{f.n}</span>
                </button>
              </li>
            );
          })}
          {facets.length > FACET_HEAD && (
            <li>
              <button type="button" onClick={() => setAll((v) => !v)} className="px-2 py-0.5 text-xs text-primary hover:underline">
                {all ? "Show fewer" : `Show all ${facets.length}`}
              </button>
            </li>
          )}
        </ul>
      )}
    </section>
  );
}

// The geometry facet is single-select (all / on-the-map / no-footprint) → radiogroup semantics;
// clicking the active row clears it back to "all". Collapsible like the others.
function GeometrySection({ facets, value, onChange }: {
  facets: FacetCount[]; value: FacetSelection["geometry"]; onChange: (v: FacetSelection["geometry"]) => void;
}) {
  const [open, setOpen] = useState(true);
  if (facets.length < 2) return null;
  return (
    <section className="border-t border-border px-2 py-2">
      <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open}
        className="flex w-full items-center justify-between px-1 py-0.5 text-left">
        <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Geometry</span>
        <span aria-hidden className="text-xs text-muted-foreground">{open ? "▾" : "▸"}</span>
      </button>
      {open && (
        <ul className="mt-1 space-y-0.5" role="radiogroup" aria-label="Geometry">
          {facets.map((f) => {
            const on = value === f.key;
            return (
              <li key={f.key}>
                <button type="button" role="radio" aria-checked={on}
                  onClick={() => onChange(on ? "all" : (f.key as FacetSelection["geometry"]))}
                  className={`flex w-full items-center gap-2 rounded px-2 py-1 text-left text-sm ${on
                    ? "bg-muted font-medium text-foreground" : "text-foreground hover:bg-muted"}`}>
                  <span aria-hidden className={`grid h-4 w-4 shrink-0 place-items-center rounded-full border text-[10px] leading-none ${on
                    ? "border-primary bg-primary text-primary-foreground" : "border-border bg-card"}`}>{on ? "●" : ""}</span>
                  <span className="min-w-0 flex-1 truncate">{f.label}</span>
                  <span className="shrink-0 tabular-nums text-xs text-muted-foreground">{f.n}</span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

type CardProps = { it: ItemRef; density: Density; on: boolean; link: LinkAttrs };

// collection · type · date, as a muted meta line (reference parity — text, not a badge wall).
const metaLine = (it: ItemRef) => [collectionLabel(it.collId), typeOf(it), dateOf(it)].filter(Boolean).join(" · ");
// Muted, NON-anchor format chips — the card is itself an <a>, so it must contain no nested anchors.
// Thumbnails/images are already the card image, so they're dropped.
const formatBadges = (it: ItemRef) =>
  Object.entries(it.data?.assets ?? {})
    .filter(([, a]) => !a.roles?.includes("thumbnail") && !a.type?.startsWith("image/"))
    .slice(0, 4)
    .map(([k, a]) => <span key={k} className={C.badge}>{a.title ?? k}</span>);

const FOCUS_RING = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

// Gallery card — the centerpiece: thumbnail + title + series + meta (+ format chips when roomy). The
// whole card is one <a>, so it's keyboard-operable and cmd/middle-click opens a new tab.
function ResultCard({ it, density, on, link }: CardProps) {
  const th = thumbnailAsset(it.data);
  const compact = density === "compact";
  return (
    <a {...link}
      className={`flex cursor-pointer gap-3 rounded-lg border bg-card p-3 text-inherit no-underline transition hover:border-primary hover:shadow-sm ${FOCUS_RING} ${on ? "border-primary ring-1 ring-primary" : "border-border"}`}>
      <div className={`${compact ? "h-12 w-12" : "h-20 w-20"} flex shrink-0 items-center justify-center overflow-hidden rounded-md border border-border bg-muted`}>
        {th ? <img src={th.href} alt="" loading="lazy" className="h-full w-full object-cover" />
          : <span className="px-1 text-center font-mono text-[10px] leading-tight text-muted-foreground">{discoverySeries(it)}</span>}
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-1.5">
          <span className="truncate font-mono text-[11px] font-semibold text-foreground" title={discoverySeries(it)}>{discoverySeries(it)}</span>
          {hasGeometry(it) && <span className="shrink-0 text-[10px] font-medium text-primary"><span aria-hidden>◆</span> map</span>}
        </div>
        <p className={`font-semibold leading-tight text-foreground ${compact ? "line-clamp-1" : "line-clamp-2"} text-sm`}>{discoveryTitle(it)}</p>
        <div className="mt-1 truncate text-xs text-muted-foreground" title={metaLine(it)}>{metaLine(it)}</div>
        {!compact && <div className="mt-1">{formatBadges(it)}</div>}
      </div>
    </a>
  );
}

// List row — one dense line for scanning many at once.
function ResultRow({ it, density, on, link }: CardProps) {
  const compact = density === "compact";
  return (
    <li>
      <a {...link}
        className={`flex cursor-pointer items-baseline gap-2 px-3 text-inherit no-underline ${compact ? "py-1" : "py-2"} ${FOCUS_RING} ${on ? "bg-primary/10" : "hover:bg-muted"}`}>
        <span className="shrink-0 font-mono text-[11px] font-semibold text-foreground">{discoverySeries(it)}</span>
        <span className="truncate text-sm text-foreground" title={discoveryTitle(it)}>{discoveryTitle(it)}</span>
        {!compact && <span className="hidden shrink-0 text-xs text-muted-foreground sm:inline">{collectionLabel(it.collId)}</span>}
        {hasGeometry(it) && (
          <span className="ml-auto shrink-0 text-primary">
            <span aria-hidden className="text-[10px]">◆</span><span className="sr-only">on the map</span>
          </span>
        )}
      </a>
    </li>
  );
}
