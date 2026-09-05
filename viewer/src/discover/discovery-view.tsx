// The full-width Discover view: a GDR-style split of a LEFT facet rail · CENTER result cards (the
// star) · RIGHT co-equal map, over every loaded catalog item. It reuses the viewer's own plumbing —
// the shared MiniSearch index (search-index), the pure facet/filter/sort core (discovery-model), the
// map ItemMap (footprints + hover/bounds sync), and the existing ItemDetail for the selection drawer
// — restyled onto the Utah Design System tokens + shared controls (UiSegmented / UiSelect), no new deps.
// All pure logic lives in ./discovery-model; this file is the React shell + the map/detail wiring.
import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";

import type { ItemRef } from "@/catalog/browse";
import {
  activeChips, applyFacets, discoveryPatch, type DiscoveryState, discoveryTitle, docIdOf,
  extractFacets, type FacetCount, type FacetSelection, filterByViewport, parseDiscovery, sortItems,
  type SortKey, SORTS,
} from "./discovery-model";
import { categoryLabel, collectionLabel, itemIdOf } from "@/catalog/item-view";
import type { Footprint } from "@/map/map-model";
import { itemLink, type LinkAttrs, ResultCard, ResultRow } from "@/catalog/result-card";
import { useQuery } from "@tanstack/react-query";

import { ArticleHit, useCorpus } from "./article-search";
import { searchPubs } from "./ftsearch";
import { buildIndex, type Hit, toSearchDoc } from "./search-index";
import type { StacDoc } from "@/stac";
import { ItemDetail } from "@/catalog/item-detail";
import { UiSegmented } from "@/ui/segmented";
import { UiSelect } from "@/ui/select";
import { useIsWide } from "@/ui/use-breakpoint";
import { ResizeHandle } from "@/ui/resizable";
import { useResizable } from "@/ui/use-resizable";

// maplibre is ~1.5MB — lazy so the rail + cards paint immediately and the map streams in behind them
// (App already code-splits ./map, so this shares that chunk).
const ItemMap = lazy(() => import("@/map/map").then((m) => ({ default: m.ItemMap })));

const LAYOUTS = [{ value: "gallery" as const, label: "Gallery" }, { value: "list" as const, label: "List" }];
const DENSITIES = [{ value: "comfortable" as const, label: "Comfy" }, { value: "compact" as const, label: "Compact" }];
const SORT_ITEMS = SORTS.map((s) => ({ value: s.key, label: s.label }));
const PAGE = 48; // cards per "Show more" step (reference parity)
// Detail drawer width: drag-resizable and remembered, since how much room the preview deserves
// depends on the item (a long abstract vs. a thumbnail). CSS caps it on narrow viewports.
const DRAWER_KEY = "ugsw.discoverDrawerW";
const DRAWER = { initial: 560, min: 360, max: 1100 };

const idOf = (href: string) => href.split("/").slice(-2)[0];
// Escape a value for a [data-href="…"] selector (scroll a map-hovered card into view).
const escAttr = (s: string) => s.replace(/["\\]/g, "\\$&");

export function DiscoveryView({
  items, itemsKey, onOpenItem, onOpenPub, itemSelected, selectedItem, selectedCollectionId, onCloseItem, onViewOnMap, onExplore,
}: {
  items: ItemRef[];
  itemsKey: string; // stable identity for the (deliberately unmemoized) items array — App's mapLoadKey
  onOpenItem: (href: string) => void;   // the map footprint picker; cards navigate via <Link>
  onOpenPub: (collId: string, itemId: string) => void;  // an article cites a pub by series id
  itemSelected: boolean;               // an item is selected (?i=) → show the detail drawer
  selectedItem?: StacDoc;              // its full doc (App resolves it from ?c=/?i=); undefined while loading
  selectedCollectionId?: string;
  onCloseItem: () => void;             // clears ?i=
  onViewOnMap: () => void;             // opens the selected item on the Map view
  onExplore?: () => void;              // opens the selected item full-screen in the Preview view
}) {
  const navigate = useNavigate();
  // The whole filter/sort/layout state lives in the URL (namespaced Discover keys), so a landing tile,
  // a shared link, or the Back button reproduces the view. App still owns view/c/i/l/s; we patch only
  // our own keys. parse is cheap → recomputed each render; the memos below key on the SERIALIZED values
  // (not the arrays, which are fresh each parse) so they don't re-run on unrelated renders.
  const sp = useSearch({ from: "__root__" });
  const st = parseDiscovery(sp);
  const { q, geometry, sort, layout, density, area } = st;
  const { collections: colls, categories: cats, types, formats } = st;
  const collsK = colls.join("|"), catsK = cats.join("|"), typesK = types.join("|"), formatsK = formats.join("|");
  const areaK = area ? area.join(",") : "";

  // Merge a partial state change into the URL. push (default) for discrete filter changes so Back
  // undoes them one at a time; replace for typing + view prefs (layout/density) so they don't pile up.
  const patch = (p: Partial<DiscoveryState>, replace = false) => {
    const next = discoveryPatch({ ...st, ...p });
    // `to: "."` is the current route — a same-route search patch, and it is what types the reducer.
    navigate({ to: ".", replace, search: (prev) => ({ ...prev, ...next }) });
  };
  const toggleList = (key: "collections" | "categories" | "types" | "formats", value: string) => {
    const cur = st[key];
    patch({ [key]: cur.includes(value) ? cur.filter((k) => k !== value) : [...cur, value] });
  };

  // Ephemeral UI state (never shareable): the map toggle, the hover highlight, the live viewport, and
  // how many cards are rendered.
  const [showMap, setShowMap] = useState(true);
  const [hoverHref, setHoverHref] = useState<string | null>(null);
  const [bounds, setBounds] = useState<[number, number, number, number] | null>(null); // live viewport
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
  // Survey Notes articles, in a SECOND index. Not merged into the item index: an article has no
  // collection, geometry or date, so it cannot ride the ItemRef pipeline the facets/map/sort use.
  // Lazy — nothing fetches the corpus until someone actually types.
  const corpus = useCorpus(q.trim().length >= 2);
  const articleIndex = useMemo(
    () => (corpus.data?.length ? buildIndex(corpus.data, []).index : null),
    [corpus.data],
  );
  // "DS-9" -> its collection, from the loaded items; the series prefix is the fallback for a pub
  // that has not streamed in yet.
  const collOfPub = useMemo(() => {
    const m = new Map<string, string>();
    for (const it of withData) m.set(itemIdOf(it).toUpperCase(), it.collId);
    return m;
  }, [itemsKey]); // eslint-disable-line react-hooks/exhaustive-deps
  const openPub = (sid: string) =>
    onOpenPub(collOfPub.get(sid.toUpperCase()) ?? (sid.match(/^[A-Za-z]+/)?.[0]?.toUpperCase() ?? sid), sid);

  // BM25 over every publication's full text (~7000 docs). Opt-in: it downloads a query engine on
  // first use, so it stays off until someone ticks it rather than firing on every keystroke.
  const [pubText, setPubText] = useState(false);
  const pubFts = useQuery({
    queryKey: ["pub-fts", q.trim()],
    enabled: pubText && q.trim().length >= 2,
    staleTime: Infinity, retry: false,
    queryFn: () => searchPubs(q.trim()),
  });

  const articleHits = useMemo(() => {
    const query = q.trim();
    if (!articleIndex || query.length < 2) return [];
    return (articleIndex.search(query) as unknown as Hit[]).slice(0, 20);
  }, [articleIndex, q]);

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
    let base = applyFacets(queried, { collections: colls, categories: cats, types, formats, geometry });
    if (area) base = filterByViewport(base, area);
    return sortItems(base, sort);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [queried, collsK, catsK, typesK, formatsK, geometry, areaK, sort]);

  // Reset paging whenever the working set changes (new query/filter/sort) — reference parity. The
  // listed keys are all serialized primitives (none referenced in the body), so no disable is needed.
  useEffect(() => setVisible(PAGE), [q, collsK, catsK, typesK, formatsK, geometry, areaK, sort, itemsKey]);
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
  // Below lg the drawer would be a cramped column beside a dead sliver of list, so a result goes to
  // the full item page instead. One descriptor drives both the click and the href.
  const cardLink = (it: ItemRef): LinkAttrs => ({
    ...itemLink(it, isWide),
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

  // The removable filter chips (pure), and a one-shot reset of every filter (text + sort/layout kept).
  const drawer = useResizable(DRAWER_KEY, DRAWER, "x-left");

  const chips = activeChips(st, { collection: collectionLabel, category: categoryLabel });
  const activeFilters = chips.length;
  const resetAll = () => patch({ collections: [], categories: [], types: [], formats: [], geometry: "all", area: null });

  return (
    <div className="relative flex h-full min-h-0 flex-col overflow-hidden bg-background text-foreground">
      {/* ── Top bar: search · count · (map-area) · sort · density · layout · map toggle ────────── */}
      <div className="flex flex-wrap items-center gap-2 border-b border-border bg-background px-3 py-2">
        <input value={q} onChange={(e) => patch({ q: e.target.value }, true)}
          placeholder="Search layers, publications and article text…" aria-label="Search the catalog"
          className="min-w-[12rem] flex-1 rounded-md border border-input bg-card px-3 py-1.5 text-sm text-foreground outline-none placeholder:text-muted-foreground focus:border-primary sm:max-w-md" />
        {/* Off by default: ticking it downloads a DuckDB query engine, so it is a deliberate act
            rather than something every keystroke pays for. */}
        <label className="flex shrink-0 cursor-pointer items-center gap-1.5 text-xs text-muted-foreground"
          title="Search inside every publication's text (~7000 docs; loads a query engine on first use)">
          <input type="checkbox" checked={pubText} onChange={(e) => setPubText(e.target.checked)} />
          Publication text
        </label>
        <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
          <b className="text-foreground">{results.length}</b> of {withData.length}
        </span>
        {/* Clearable even when the map is hidden — the map's own "clear area" pill can be off-screen. */}
        {area && (
          <button type="button" onClick={() => patch({ area: null })}
            title="Results are limited to the map area — click to clear"
            className="shrink-0 rounded-full border border-primary bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary hover:bg-primary/20">
            Map area ✕
          </button>
        )}
        <div className="ml-auto flex flex-wrap items-center gap-1.5">
          <label className="flex items-center gap-1 text-xs text-muted-foreground">
            Sort
            <UiSelect value={sort} onValueChange={(v) => patch({ sort: v as SortKey })} items={SORT_ITEMS} className="text-xs" />
          </label>
          <UiSegmented value={density} onValueChange={(v) => patch({ density: v }, true)} items={DENSITIES} className="text-xs" />
          <UiSegmented value={layout} onValueChange={(v) => patch({ layout: v }, true)} items={LAYOUTS} className="text-xs" />
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
          <FacetSection label="Category" facets={facets.categories} selected={new Set(cats)}
            onToggle={(k) => toggleList("categories", k)} />
          <FacetSection label="Collection" facets={facets.collections} selected={new Set(colls)}
            onToggle={(k) => toggleList("collections", k)} />
          <FacetSection label="Type" facets={facets.types} selected={new Set(types)}
            onToggle={(k) => toggleList("types", k)} />
          <FacetSection label="Format" facets={facets.formats} selected={new Set(formats)}
            onToggle={(k) => toggleList("formats", k)} />
          <GeometrySection facets={facets.geometry} value={geometry} onChange={(v) => patch({ geometry: v })} />
          <div className="px-3 py-4 text-[11px] leading-snug text-muted-foreground">
            Filters narrow the cards and the map together. Hover a card to find it on the map.
          </div>
        </aside>

        {/* CENTER — result cards (the star): gallery grid or list, paginated. */}
        <div ref={listRef} className="min-h-0 flex-1 overflow-y-auto bg-muted/30 px-3 py-3">
          {/* Removable active-filter chips — a legible summary of what's narrowing the set, above the
              cards (the rail is md+ only, so on a phone this is the ONLY way to see/clear a filter). */}
          {chips.length > 0 && (
            <div className="mb-2 flex flex-wrap items-center gap-1.5">
              {chips.map((c) => (
                <button key={c.id} type="button" onClick={() => patch(c.patch)}
                  className="inline-flex items-center gap-1 rounded-full border border-primary bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary hover:bg-primary/20">
                  {c.label} <span aria-hidden>✕</span>
                  <span className="sr-only">remove filter</span>
                </button>
              ))}
              <button type="button" onClick={resetAll} className="px-1 text-xs text-muted-foreground hover:text-foreground hover:underline">
                Clear all
              </button>
            </div>
          )}
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
            <div className="grid gap-3 [grid-template-columns:repeat(auto-fill,minmax(min(240px,100%),1fr))]">
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

          {/* Articles are their OWN group, not merged into the cards above: a Survey Notes article
              has no collection, geometry or date, so the facets, sort and map beside it do not
              apply to one. Same query, second corpus. */}
          {pubText && q.trim().length >= 2 && (
            <section className="mt-6">
              <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
                Publication full text{pubFts.data ? ` · ${pubFts.data.length}` : ""}
              </h2>
              {pubFts.isLoading && <p className="mt-1 text-xs text-muted-foreground">Loading the query engine + searching…</p>}
              {pubFts.isError && <p className="mt-1 text-xs text-muted-foreground">Full-text index not available yet (built by the FTS job on reingest).</p>}
              <ol className="mt-1 divide-y divide-border">
                {(pubFts.data ?? []).map((r) => (
                  <li key={r.id} className="py-2">
                    <div className="flex flex-wrap items-baseline gap-x-2">
                      {r.series && <span className="rounded bg-muted px-1.5 text-xs uppercase text-muted-foreground">{r.series}</span>}
                      <span className="font-medium text-foreground">{r.title}</span>
                      <span className="font-mono text-xs text-muted-foreground">{r.id}</span>
                    </div>
                    <div className="mt-1 flex flex-wrap gap-x-3 text-xs">
                      {r.pdf && <a href={r.pdf} target="_blank" rel="noopener" className="text-primary hover:underline">Open PDF ↗</a>}
                      <button className="text-primary hover:underline" onClick={() => openPub(r.id)}>Catalog page</button>
                    </div>
                  </li>
                ))}
              </ol>
            </section>
          )}

          {articleHits.length > 0 && (
            <section className="mt-6">
              <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
                Survey Notes articles · {articleHits.length}
              </h2>
              <ol className="mt-1 divide-y divide-border">
                {articleHits.map((r) => (
                  <ArticleHit key={r.id} r={r} q={q} openPub={openPub} />
                ))}
              </ol>
            </section>
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
            {/* Below the map's own controls, not level with them: the geocoder (left-2 top-2) and
                the basemap/coverage cluster (right-2 top-2) are separately positioned, and centring
                this on a 2/5-width pane put it on top of the basemap toggle. */}
            <div className="pointer-events-none absolute inset-x-0 top-12 z-10 flex justify-center">
              {area ? (
                <button type="button" onClick={() => patch({ area: null })}
                  className="pointer-events-auto rounded-full border border-primary bg-primary px-3 py-1 text-xs font-medium text-primary-foreground shadow">
                  ✕ Clear map area
                </button>
              ) : (
                <button type="button" onClick={() => bounds && patch({ area: bounds })} disabled={!bounds}
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
            style={isWide ? { width: drawer.size } : undefined}
            className="absolute inset-y-0 right-0 z-30 flex w-full flex-col border-l border-border bg-background shadow-xl lg:max-w-[calc(100vw-2rem)]">
            {/* Drag (or arrow-key) the left edge to widen the preview. Full-bleed below lg, so the
                handle only exists where there's something to trade width with. */}
            {isWide && <ResizeHandle resizable={drawer} label="Resize item detail" className="absolute inset-y-0 -left-2 z-10" />}
            <div className="flex shrink-0 items-center justify-between border-b border-border px-4 py-2">
              <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Item detail</span>
              <div className="flex items-center gap-1">
                {/* The drawer is the single-column layout; the two-column page lives on Catalog. */}
                <Link to="/catalog" search={{ c: selectedCollectionId, i: selectedItem?.id }}
                  className="rounded px-2 py-1 text-sm text-muted-foreground hover:bg-muted hover:text-foreground">
                  Open full page ↗
                </Link>
                <button ref={closeRef} type="button" onClick={onCloseItem}
                  className="rounded px-2 py-1 text-sm text-muted-foreground hover:bg-muted">✕ Close</button>
              </div>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
              <ItemDetail collectionId={selectedCollectionId ?? ""} item={selectedItem} layout="drawer"
                onBack={onCloseItem} onMap={onViewOnMap} onExplore={onExplore} />
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

// The result card + list row now live in result-card.tsx (shared with the Landing "Recently updated"
// strip). This file keeps only the Discover shell + the facet-rail sections above.
