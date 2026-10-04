// The full-width Discover view: a GDR-style split of a LEFT facet rail · CENTER result cards (the
// star) · RIGHT co-equal map, over every loaded catalog item. It reuses the viewer's own plumbing —
// the shared MiniSearch index (search-index), the pure facet/filter/sort core (discovery-model), the
// map ItemMap (footprints + hover/bounds sync), and the existing ItemDetail for the selection drawer
// — restyled onto the Utah Design System tokens + shared controls (UiSegmented / UiSelect), no new deps.
// All pure logic lives in ./discovery-model; this file is the React shell + the map/detail wiring.
import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { qk } from "@/query-keys";

import type { ItemRef } from "@/catalog/browse";
import {
  activeChips, CLEAR_ALL, discoveryPatch, type DiscoveryState, discoveryTitle, docIdOf, effectiveSort,
  extractFacets, type FacetCount, GEOM_HAS, withSelected, type FacetSelection, filterResults, nearestStep, parseDiscovery,
  publishedYear, ranksByWords, reliefs, SCALE_STEPS, scaleBins, scalesLabel, sortItems, type SortKey, SORTS, yearBins,
  yearsLabel, yearSpan,
} from "./discovery-model";
import { RangeFacet } from "./range-facet";
import { categoryLabel, collectionLabel, itemIdOf, scaleDenominator } from "@/catalog/item-view";
import type { Footprint } from "@/map/map-model";
import { AddToMapButton } from "@/map/add-to-map-button";
import { OpenMapPill } from "./open-map-pill";
import { itemLink, type LinkAttrs, ResultCard, ResultRow } from "@/catalog/result-card";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Dialog } from "@base-ui/react/dialog";

import { ArticleHit, useArticleSearch } from "./article-search";
import { searchPubs } from "./ftsearch";
import { baseTerms, isEmptyQuery, matchesQuery, parseQuery, type SearchDoc } from "@/data/query";
import { catalogIndex, type Hit, idMatch, searchCatalog } from "./search-index";
import { type Bounds, locate, suggest } from "@/map/place-locator";
import { flyTo } from "@/map/camera";
import { LiveSearchBar } from "@/shell/map-search";
import type { StacDoc } from "@/stac";
import { ItemDetail } from "@/catalog/item-detail";
import { UiSegmented } from "@/ui/segmented";
import { UiSelect } from "@/ui/select";
import { useIsDesktop, useIsWide } from "@/ui/use-breakpoint";
import { ResizeHandle } from "@/ui/resizable";
import { useResizable } from "@/ui/use-resizable";

// maplibre is ~1.5MB — lazy so the rail + cards paint immediately and the map streams in behind them
// (App already code-splits ./map, so this shares that chunk).
const ItemMap = lazy(() => import("@/map/map").then((m) => ({ default: m.ItemMap })));

const LAYOUTS = [{ value: "gallery" as const, label: "Gallery" }, { value: "list" as const, label: "List" }];
const DENSITIES = [{ value: "comfortable" as const, label: "Comfy" }, { value: "compact" as const, label: "Compact" }];
const SORT_ITEMS = SORTS.map((s) => ({ value: s.key, label: s.label }));
const SORT_ITEMS_WITHOUT_MATCH = SORT_ITEMS.filter((s) => s.value !== "relevance");
const PAGE = 48; // cards per "Show more" step (reference parity)
const TYPE_DEBOUNCE_MS = 180;
const SCALE_MAX = SCALE_STEPS.length - 1;
const FILTERS_DIALOG = Dialog.createHandle();   // ties the phone Filters button to its panel
const fmtDenom = (d: number) => d.toLocaleString("en-US");
// Detail drawer width: drag-resizable and remembered, since how much room the preview deserves
// depends on the item (a long abstract vs. a thumbnail). CSS caps it on narrow viewports.
const DRAWER_KEY = "ugsw.discoverDrawerW";
const DRAWER = { initial: 560, min: 360, max: 1100 };

const idOf = (href: string) => href.split("/").slice(-2)[0];
// Escape a value for a [data-href="…"] selector (scroll a map-hovered card into view).
const escAttr = (s: string) => s.replace(/["\\]/g, "\\$&");

export function DiscoveryView({
  items, itemsKey, loading = false, onOpenItem, onOpenPub, itemSelected, selectedItem, selectedItemError, selectedCollectionId, onCloseItem, onViewOnMap, onExplore,
}: {
  items: ItemRef[];
  itemsKey: string; // stable identity for the (deliberately unmemoized) items array — App's mapLoadKey
  loading?: boolean; // the catalog is still streaming in, so category counts are not final yet
  onOpenItem: (href: string) => void;   // the map footprint picker; cards navigate via <Link>
  onOpenPub: (collId: string, itemId: string) => void;  // an article cites a pub by series id
  itemSelected: boolean;               // an item is selected (?i=) → show the detail drawer
  selectedItem?: StacDoc;              // its full doc (App resolves it from ?c=/?i=); undefined while loading
  selectedItemError?: unknown;         // that doc's request error (e.g. offline and never cached)
  selectedCollectionId?: string;
  onCloseItem: () => void;             // clears ?i=
  onViewOnMap: () => void;             // opens the selected item on the Map view
  onExplore?: () => void;              // opens the selected item full-screen in the Preview view
}) {
  const navigate = useNavigate();
  // The whole filter/sort/layout state lives in the URL (namespaced Discover keys), so a category tile,
  // a shared link, or the Back button reproduces the view. App still owns view/c/i/l/s; we patch only
  // our own keys. parse is cheap → recomputed each render; the memos below key on the SERIALIZED values
  // (not the arrays, which are fresh each parse) so they don't re-run on unrelated renders.
  const sp = useSearch({ from: "__root__" });
  const st = parseDiscovery(sp);
  const { q, geometry, layout, density, area } = st;
  const sort = effectiveSort(st);
  const { collections: colls, categories: cats, types, formats } = st;
  const collsK = colls.join("|"), catsK = cats.join("|"), typesK = types.join("|"), formatsK = formats.join("|");
  const areaK = area ? area.join(",") : "";
  const yearsK = st.years ? st.years.join(",") : "", scalesK = st.scales ? st.scales.join(",") : "";
  const filterK = [collsK, catsK, typesK, formatsK, geometry, areaK, yearsK, scalesK].join("~");

  // Merge a partial state change into the URL. push (default) for discrete filter changes so Back
  // undoes them one at a time; replace for typing + view prefs (layout/density) so they don't pile up.
  // It merges onto the URL as it is when the navigation runs, not this render's `st`, so a delayed
  // patch (the typing debounce, a place lookup) can't undo a filter set in the meantime.
  // While the phone filter panel is open its changes replace the history entry, so the phone's Back
  // closes the panel rather than undoing filters one by one behind it.
  const inPanel = useRef(false);
  const patch = (p: Partial<DiscoveryState>, replace = false) => {
    // `to: "."` is the current route — a same-route search patch, and it is what types the reducer.
    navigate({ to: ".", replace: replace || inPanel.current,
      search: (prev) => ({ ...prev, ...discoveryPatch({ ...parseDiscovery(prev), ...p }) }) });
  };
  const toggleList = (key: "collections" | "categories" | "types" | "formats", value: string) => {
    const cur = st[key];
    patch({ [key]: cur.includes(value) ? cur.filter((k) => k !== value) : [...cur, value] });
  };

  // ?q= follows the box after a pause; a q changed from outside (Back, a chip) resets the box.
  const [text, setText] = useState(q);
  const sentQ = useRef(q);
  const typeTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => {
    if (q === sentQ.current) return;
    clearTimeout(typeTimer.current);   // or the pending keystrokes would overwrite where Back landed
    sentQ.current = q;
    setText(q);
  }, [q]);
  useEffect(() => () => clearTimeout(typeTimer.current), []);
  const sendQ = (v: string, now = false) => {
    clearTimeout(typeTimer.current);
    const go = () => { sentQ.current = v; patch({ q: v }, true); };
    if (now) go(); else typeTimer.current = setTimeout(go, TYPE_DEBOUNCE_MS);
  };
  // The Survey Notes text is 8.5 MB, and indexing it blocks the page for about a second, so it loads
  // only once someone asks for it: Enter, or the articles chip.
  const [deep, setDeep] = useState(false);
  // Publication full text is a remote query per search, so it runs on Enter (or its chip), not on
  // every pause in typing.
  const [ftsQ, setFtsQ] = useState("");
  const submit = () => {
    sendQ(text, true);
    if (text.trim().length >= 2) { setDeep(true); setFtsQ(text.trim()); }
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
  const { index, docs: itemDocs } = useMemo(
    () => catalogIndex(itemsKey, withData),
    [itemsKey], // eslint-disable-line react-hooks/exhaustive-deps
  );
  // Survey Notes articles, in a SECOND index, built in a worker. Not merged into the item index: an
  // article has no collection, geometry or date, so it cannot ride the ItemRef pipeline the
  // facets/map/sort use.
  const articles = useArticleSearch(deep ? q : "");
  const articleHits = articles.data ?? [];
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
  // Which result kind the chips are showing. "all" stacks them; the rest isolate one.
  const [scopePick, setScope] = useState<"all" | "items" | "articles" | "pubtext">("all");
  const pubFts = useQuery({
    queryKey: qk.pubFts(ftsQ),
    enabled: pubText && ftsQ.length >= 2,
    staleTime: Infinity, retry: false,
    queryFn: () => searchPubs(ftsQ),
  });

  // "exact phrase", -exclude and series:GQ — the same parser the publication BM25 path uses, so one
  // box speaks one language across all three corpora.
  const query = useMemo(() => parseQuery(q), [q]);

  // The scope chips only show for a search, so without one (or once the chosen kind has nothing)
  // the view falls back to everything rather than an empty page with no way back.
  const scope = q.trim().length < 2 || (scopePick === "articles" && articleHits.length === 0) ? "all" : scopePick;

  // href → bbox for O(1) highlight lookup on hover (rather than scanning withData each hover render).
  const bboxByHref = useMemo(() => {
    const m = new Map<string, number[] | undefined>();
    for (const it of withData) m.set(it.href, it.data?.bbox);
    return m;
  }, [itemsKey]); // eslint-disable-line react-hooks/exhaustive-deps

  // Text narrows first (score-ordered via the shared index); facets/area/sort are pure and cheap.
  const queried = useMemo(() => {
    if (q.trim().length < 2 && isEmptyQuery(query)) return withData;
    // Bare/phrase words narrow via MiniSearch; a field- or exclude-only query has no keyword to
    // hand it, so scan the flat doc list instead.
    const base = baseTerms(query);
    const hits = base ? (searchCatalog(index, base) as unknown as Hit[]) : itemDocs;
    const order = new Map(hits.filter((h) => matchesQuery(query, h as SearchDoc)).map((h, i) => [h.id, i]));
    return withData
      .filter((it) => order.has(docIdOf(it)))
      .sort((a, b) => (order.get(docIdOf(a)) ?? 0) - (order.get(docIdOf(b)) ?? 0));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [itemsKey, q, query, index, itemDocs]);

  // Facet counts over the search-narrowed set: they respond to the query (the primary narrowing) but
  // stay stable as you toggle facets — the rail reads as a table of contents, not a jumping wall.
  const facets = useMemo(() => extractFacets(queried), [queried]);

  const results = useMemo(() => sortItems(filterResults(queried, st), sort),
    [queried, filterK, sort]); // eslint-disable-line react-hooks/exhaustive-deps

  const searching = q.trim().length >= 2 || !isEmptyQuery(query);
  const hidden = useMemo(() => (searching && queried.length > results.length
    ? { n: queried.length - results.length, reliefs: reliefs(queried, st, results.length) }
    : null), [queried, results, searching]); // eslint-disable-line react-hooks/exhaustive-deps

  const namedId = useMemo(() => (q.trim() ? idMatch(index, q.trim())?.id : undefined), [index, q]);

  // Histograms count what the other filters leave, so they describe what you'd get.
  const span = useMemo(() => yearSpan(withData), [itemsKey]); // eslint-disable-line react-hooks/exhaustive-deps
  const yearPool = useMemo(() => filterResults(queried, st, "years"), [queried, filterK]); // eslint-disable-line react-hooks/exhaustive-deps
  const scalePool = useMemo(() => filterResults(queried, st, "scales"), [queried, filterK]); // eslint-disable-line react-hooks/exhaustive-deps
  const yearHist = useMemo(() => (span ? yearBins(yearPool, span, 5).map((b) => ({
    from: b.lo, to: b.hi, n: b.n, tip: `${b.lo} to ${b.hi}: ${b.n.toLocaleString()}` })) : []), [yearPool, span]);
  const scaleHist = useMemo(() => scaleBins(scalePool).map((b, i) => ({
    from: i, to: i, n: b.n, tip: `1:${fmtDenom(b.lo)}: ${b.n.toLocaleString()}` })), [scalePool]);
  const undated = useMemo(() => (st.years ? yearPool.filter((it) => publishedYear(it) === null).length : 0),
    [yearPool, yearsK]); // eslint-disable-line react-hooks/exhaustive-deps
  const unscaled = useMemo(() => (st.scales ? scalePool.filter((it) => scaleDenominator(it) === null).length : 0),
    [scalePool, scalesK]); // eslint-disable-line react-hooks/exhaustive-deps

  const typed = text.trim();
  const placeQ = useQuery({
    queryKey: qk.placeSuggest(typed),
    queryFn: ({ signal }) => suggest(typed, signal),
    enabled: typed.length >= 3 && !area,
    staleTime: Infinity, retry: false,
    placeholderData: keepPreviousData,   // keep the pill steady while the next keystroke's lookup runs
  });
  const placeHit = typed.length >= 3 && !area
    ? placeQ.data?.find((s) => s.text.toLowerCase().startsWith(typed.toLowerCase()))
    : undefined;
  const [placeError, setPlaceError] = useState<string | null>(null);
  useEffect(() => setPlaceError(null), [typed]);
  const [picking, setPicking] = useState(false);
  const pickPlace = async () => {
    if (!placeHit || picking) return;
    setPicking(true);
    try {
      const b: Bounds = await locate(placeHit);
      // A point (an address) has no extent; give it a small one so it can meet a footprint.
      const box: Bounds = b[0] === b[2] && b[1] === b[3] ? [b[0] - 0.02, b[1] - 0.02, b[2] + 0.02, b[3] + 0.02] : b;
      setPlaceError(null);
      clearTimeout(typeTimer.current);
      sentQ.current = "";
      setText("");
      patch({ area: box, place: placeHit.text, q: "" });
      if (isWide && showMap) flyTo(box);   // only a mounted map; a queued fit would fire on /map later
    } catch (e) {
      setPlaceError(`Couldn't find ${placeHit.text}: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setPicking(false);
    }
  };

  // Reset paging whenever the working set changes (new query/filter/sort) — reference parity. The
  // listed keys are all serialized primitives (none referenced in the body), so no disable is needed.
  useEffect(() => setVisible(PAGE), [q, filterK, sort, itemsKey]);
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
  const resetAll = () => patch(CLEAR_ALL);

  // Shared by the rail and the phone panel; on a phone most groups start closed.
  const filterSections = (phone: boolean) => (
    <>
      <FacetSection label="Category" facets={withSelected(facets.categories, cats, categoryLabel)} defaultOpen selected={new Set(cats)}
        onToggle={(k) => toggleList("categories", k)} />
      <FacetSection label="Collection" defaultOpen={!phone} facets={withSelected(facets.collections, colls, collectionLabel)} selected={new Set(colls)}
        onToggle={(k) => toggleList("collections", k)} />
      <FacetSection label="Type" defaultOpen={!phone} facets={withSelected(facets.types, types, String)} selected={new Set(types)}
        onToggle={(k) => toggleList("types", k)} />
      <FacetSection label="Format" defaultOpen={!phone} facets={withSelected(facets.formats, formats, String)} selected={new Set(formats)}
        onToggle={(k) => toggleList("formats", k)} />
      {span && span[0] < span[1] && (
        <RangeFacet label="Year published" defaultOpen summary={st.years ? yearsLabel(st.years) : "Any"}
          min={span[0]} max={span[1]} value={[st.years?.[0] ?? span[0], st.years?.[1] ?? span[1]]}
          bins={yearHist}
          onCommit={(lo, hi, replace) => patch({ years: lo <= span[0] && hi >= span[1] ? null
            : [lo <= span[0] ? null : lo, hi >= span[1] ? null : hi] }, replace)}
          toText={String} fromText={(s) => (/^\d{4}$/.test(s.trim()) ? Number(s.trim()) : null)}
          labels={["Earliest year", "Latest year"]}
          note={undated ? `${undated.toLocaleString()} without a publication year hidden` : undefined} />
      )}
      <RangeFacet label="Map scale" defaultOpen={!phone} summary={st.scales ? scalesLabel(st.scales) : "Any"}
        min={0} max={SCALE_MAX}
        value={[st.scales?.[0] != null ? nearestStep(st.scales[0]) : 0, st.scales?.[1] != null ? nearestStep(st.scales[1]) : SCALE_MAX]}
        bins={scaleHist}
        onCommit={(lo, hi, replace) => patch({ scales: lo === 0 && hi === SCALE_MAX ? null
          : [lo === 0 ? null : SCALE_STEPS[lo], hi === SCALE_MAX ? null : SCALE_STEPS[hi]] }, replace)}
        toText={(i) => fmtDenom(SCALE_STEPS[i])}
        fromText={(s) => { const n = Number(s.replace(/^\s*1\s*:/, "").replace(/[,\s]/g, "")); return n >= 1 ? nearestStep(n) : null; }}
        prefix="1:" ends={["More detailed", "Less detailed"]} labels={["Most detailed scale", "Least detailed scale"]}
        note={unscaled ? `${unscaled.toLocaleString()} without a scale hidden` : undefined} />
      <GeometrySection defaultOpen={!phone}
        facets={withSelected(facets.geometry, geometry === "all" ? [] : [geometry], (k) => (k === GEOM_HAS ? "On the map" : "No footprint"))} value={geometry} onChange={(v) => patch({ geometry: v })} />
    </>
  );
  const [filtersOpen, setFiltersOpen] = useState(false);
  const isDesktop = useIsDesktop();               // md and up: the rail shows, so the panel never does
  const panelOpen = filtersOpen && !isDesktop;
  inPanel.current = panelOpen;

  return (
    <div className="relative flex h-full min-h-0 flex-col overflow-hidden bg-background text-foreground">
      {/* The page's only h1. The header already shows the name, so it is for screen readers. */}
      <h1 className="sr-only">Discover UGS data and publications</h1>
      {/* Added layers accumulate into ?l= but Discovery's map only draws footprints — this is the
          only feedback that a card's "+ Add to map" did anything, plus the way to the Map view. */}
      <OpenMapPill />
      {/* One always-mounted live region, so a screen reader hears the count settle after typing
          (a region that mounts with its text is often never announced). */}
      <p role="status" className="sr-only">
        {withData.length ? `${results.length.toLocaleString()} ${results.length === 1 ? "result" : "results"}` : ""}
        {hidden ? `, ${hidden.n.toLocaleString()} more hidden by filters` : ""}
      </p>
      {/* ── Top bar: search · count · (map-area) · sort · density · layout · map toggle ────────── */}
      <div className="flex flex-wrap items-center gap-2 border-b border-border bg-background px-3 py-2">
        <LiveSearchBar value={text} className="min-w-[12rem] flex-1 sm:max-w-md"
          placeholder="Search titles, series IDs, authors and places"
          onChange={(v) => { setText(v); sendQ(v); }} onEnter={submit}
          onClear={() => { setText(""); sendQ("", true); }} />
        <Dialog.Trigger handle={FILTERS_DIALOG}
          className="flex shrink-0 items-center gap-1.5 rounded-full border border-input bg-card px-3 py-1 text-sm text-foreground hover:border-primary md:hidden">
          <svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M4 6h16M7 12h10M10 18h4" /></svg>
          Filters
          {activeFilters > 0 && (
            <span className="rounded-full bg-primary px-1.5 text-xs font-semibold text-primary-foreground">
              <span className="sr-only">, </span>{activeFilters}<span className="sr-only"> active</span>
            </span>
          )}
        </Dialog.Trigger>
        <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
          <b className="text-foreground">{results.length}</b> of {withData.length}
        </span>
        <div className="ml-auto flex flex-wrap items-center gap-1.5">
          <label className="flex items-center gap-1 text-xs text-muted-foreground">
            Sort
            <UiSelect value={sort} onValueChange={(v) => patch({ sort: v as SortKey })} items={ranksByWords(query) ? SORT_ITEMS : SORT_ITEMS_WITHOUT_MATCH} className="text-xs" />
          </label>
          <UiSegmented value={density} onValueChange={(v) => patch({ density: v }, true)} items={DENSITIES} className="text-xs" />
          <UiSegmented value={layout} onValueChange={(v) => patch({ layout: v }, true)} items={LAYOUTS} className="text-xs" />
          {isWide && (
            <button type="button" onClick={() => setShowMap((v) => !v)} aria-pressed={showMap}
              title={showMap ? "Hide the map" : "Show the map"}
              className={`shrink-0 rounded-md border px-2.5 py-1 text-sm ${showMap
                ? "border-primary bg-primary text-primary-foreground"
                : "border-input bg-card text-foreground hover:bg-hover"}`}>
              Map
            </button>
          )}
        </div>
      </div>

      {/* What's narrowing the results, right under the box that searches them, so a filter set earlier
          can't be forgotten. On a phone (no rail) this is the only place to see or clear one. */}
      {chips.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5 border-b border-border bg-background px-3 py-1.5">
          <span className="text-xs text-muted-foreground">Filtering by</span>
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

      {/* Result kinds, named with their counts. Without this the article and publication groups sat
          below a screenful of cards with nothing saying they existed, and the opt-in engine was a
          bare checkbox beside the item count — which read as that count's label. */}
      {q.trim().length >= 2 && (
        <div className="flex flex-wrap items-center gap-1.5 border-b border-border bg-background px-3 py-1.5 text-xs">
          <span className="text-muted-foreground">Showing</span>
          <ScopeChip on={scope === "all"} onClick={() => setScope("all")}>Everything</ScopeChip>
          <ScopeChip on={scope === "items"} onClick={() => setScope("items")}>
            Layers &amp; publications · {results.length}
          </ScopeChip>
          {articleHits.length > 0 ? (
            <ScopeChip on={scope === "articles"} onClick={() => setScope("articles")}>
              Survey Notes articles · {articleHits.length}
            </ScopeChip>
          ) : !deep ? (
            <ScopeChip on={false} onClick={() => setDeep(true)}
              title="Searches the text of every Survey Notes article (an 8.5 MB download, then cached). Enter does this too.">
              Search Survey Notes articles
            </ScopeChip>
          ) : articles.isError ? (
            <span role="alert" className="text-destructive">
              Survey Notes articles couldn't load: {articles.error instanceof Error ? articles.error.message : String(articles.error)}
            </span>
          ) : articles.isLoading ? (
            <span className="text-muted-foreground">Survey Notes articles · loading…</span>
          ) : (
            <span className="text-muted-foreground">Survey Notes articles · 0</span>
          )}
          {/* Selecting it is what starts the search, and it is a ~35MB DuckDB-WASM download on first
              use. The size goes ON the chip: a user deciding whether to click deserves the cost, not
              a tooltip. It is cached afterwards, hence "first use" rather than per search. */}
          <ScopeChip on={scope === "pubtext"} onClick={() => { setScope("pubtext"); setPubText(true); setFtsQ(q.trim()); }}
            title="Searches inside every publication's full text (~7000 docs). First use downloads a
                   ~35MB query engine, then it is cached; the index itself is read in ranges, not downloaded.">
            {pubFts.data ? `Publication text · ${pubFts.data.length}`
              : pubFts.isLoading ? "Publication text · searching…"
                : pubText ? "Publication text · loading engine…" : "Search publication text (~35MB)"}
          </ScopeChip>
          {placeHit && (
            <ScopeChip on={false} onClick={pickPlace} title={`Show everything whose footprint covers ${placeHit.text}`}>
              <span className="inline-flex items-center gap-1">
                <svg aria-hidden="true" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M12 21s-7-6.2-7-11.5A7 7 0 0 1 19 9.5C19 14.8 12 21 12 21z" /><circle cx="12" cy="9.5" r="2.5" />
                </svg>
                {picking ? `Finding ${placeHit.text}…` : `Near ${placeHit.text}`}
              </span>
            </ScopeChip>
          )}
          {(placeError || (placeQ.isError && typed.length >= 3)) && (
            <span role="alert" className="text-destructive">
              {placeError ?? `Place lookup failed: ${placeQ.error instanceof Error ? placeQ.error.message : String(placeQ.error)}`}
            </span>
          )}
        </div>
      )}

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
          {filterSections(false)}
          <div className="px-3 py-4 text-[11px] leading-snug text-muted-foreground">
            Filters narrow the cards and the map together. Hover a card to find it on the map.
          </div>
        </aside>

        {/* CENTER — result cards (the star): gallery grid or list, paginated. */}
        <div ref={listRef} className="min-h-0 flex-1 overflow-y-auto bg-muted/30 px-3 py-3">
          {hidden && (
            <div className="mb-3 space-y-2 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm dark:border-amber-700 dark:bg-amber-950/40">
              <p className="text-foreground">
                {results.length === 0
                  ? <><b>No results for “{q.trim()}” with your filters.</b> {hidden.n.toLocaleString()} match without them.</>
                  : <><b>{hidden.n.toLocaleString()} more {hidden.n === 1 ? "result" : "results"} for “{q.trim()}”</b> {hidden.n === 1 ? "is" : "are"} outside your filters.</>}
              </p>
              <div className="flex flex-wrap gap-1.5">
                <button type="button" onClick={resetAll}
                  className="rounded-full border border-primary bg-primary px-3 py-0.5 text-xs font-medium text-primary-foreground hover:bg-primary/90">
                  Search everything
                </button>
                {hidden.reliefs.map((r) => (
                  <button key={r.group} type="button" onClick={() => patch(r.patch)}
                    className="rounded-full border border-border bg-card px-3 py-0.5 text-xs text-foreground hover:border-primary">
                    {r.label} (+{r.gain.toLocaleString()})
                  </button>
                ))}
              </div>
            </div>
          )}
          {/* An isolating chip hides the item results entirely — not just their heading. */}
          {scope === "articles" || scope === "pubtext" ? null
            : withData.length === 0 ? (
            <p className="px-1 py-16 text-center text-sm text-muted-foreground">Loading the catalog…</p>
          ) : shown.length === 0 && hidden ? null : shown.length === 0 ? (
            <div className="mx-auto mt-10 max-w-sm rounded-lg border border-dashed border-border p-8 text-center">
              <p className="text-sm font-medium text-foreground">Nothing matches these filters.</p>
              {activeFilters > 0 && (
                <button type="button" onClick={resetAll} className="mt-2 text-xs text-primary hover:underline">Clear all filters</button>
              )}
            </div>
          ) : (
            <>
            {scope === "all" && q.trim().length >= 2 && (articleHits.length > 0 || pubText) && (
              <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
                Layers &amp; publications · {results.length}
              </h2>
            )}
            {layout === "gallery" ? (
            <div className="grid gap-3 [grid-template-columns:repeat(auto-fill,minmax(min(240px,100%),1fr))]">
              {shown.map((it) => (
                <ResultCard key={it.href} it={it} density={density} on={hoverHref === it.href} link={cardLink(it)}
                  idMatch={namedId === docIdOf(it)}
                  addSlot={<AddToMapButton layerId={idOf(it.href)} compact />} />
              ))}
            </div>
          ) : (
            <ul className="divide-y divide-border overflow-hidden rounded-lg border border-border bg-card">
              {shown.map((it) => (
                <ResultRow key={it.href} it={it} density={density} on={hoverHref === it.href} link={cardLink(it)}
                  idMatch={namedId === docIdOf(it)}
                  addSlot={<AddToMapButton layerId={idOf(it.href)} compact />} />
              ))}
            </ul>
            )}
            </>
          )}
          {results.length > shown.length && scope !== "articles" && scope !== "pubtext" && (
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
          {articleHits.length > 0 && scope !== "items" && scope !== "pubtext" && (
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

          {pubText && q.trim().length >= 2 && scope !== "items" && scope !== "articles" && (
            <section className="mt-6">
              <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
                Publication full text{pubFts.data ? ` · ${pubFts.data.length}` : ""}
              </h2>
              {ftsQ !== q.trim() && (
                <p className="mt-1 text-xs text-muted-foreground">For “{ftsQ}”. Press Enter to search the text for “{q.trim()}”.</p>
              )}
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
                <button type="button" onClick={() => patch({ area: null, place: "" })}
                  className="pointer-events-auto rounded-full border border-primary bg-primary px-3 py-1 text-xs font-medium text-primary-foreground shadow">
                  ✕ Clear map area
                </button>
              ) : (
                <button type="button" onClick={() => bounds && patch({ area: bounds, place: "" })} disabled={!bounds}
                  title="Limit results to what's in the current map view"
                  className="pointer-events-auto rounded-full border border-border bg-card/95 px-3 py-1 text-xs font-medium text-foreground shadow hover:bg-hover disabled:opacity-50">
                  Search this area
                </button>
              )}
            </div>
          </div>
        )}
      </div>

      <Dialog.Root handle={FILTERS_DIALOG} open={panelOpen} onOpenChange={(open) => {
        // Commit a half-typed From/To box before the panel unmounts it (a tapped button doesn't take
        // focus on iOS, so its blur would never fire).
        if (!open) (document.activeElement as HTMLElement | null)?.blur();
        setFiltersOpen(open);
      }}>
        <Dialog.Portal>
          <Dialog.Backdrop className="fixed inset-0 z-[3100] bg-black/40" />
          <Dialog.Popup aria-modal="true" className="fixed inset-0 z-[3101] flex flex-col bg-background">
            <div className="flex shrink-0 items-center gap-2 border-b border-border px-3 py-2">
              <Dialog.Title className="flex-1 text-base font-semibold">Filters</Dialog.Title>
              {activeFilters > 0 && (
                <button type="button" onClick={resetAll} className="px-2 text-sm text-primary hover:underline">Clear all</button>
              )}
              <Dialog.Close aria-label="Close filters"
                className="rounded px-2 text-muted-foreground hover:text-foreground">✕</Dialog.Close>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto">{filterSections(true)}</div>
            <div className="shrink-0 border-t border-border p-3">
              <Dialog.Close className="w-full rounded-md bg-primary px-4 py-2.5 text-sm font-semibold text-primary-foreground hover:bg-primary/90">
                Show {results.length.toLocaleString()} {results.length === 1 ? "result" : "results"}
              </Dialog.Close>
            </div>
          </Dialog.Popup>
        </Dialog.Portal>
      </Dialog.Root>

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
                  className="rounded px-2 py-1 text-sm text-muted-foreground hover:bg-hover hover:text-foreground">
                  Open full page ↗
                </Link>
                <button ref={closeRef} type="button" onClick={onCloseItem}
                  className="rounded px-2 py-1 text-sm text-muted-foreground hover:bg-hover">✕ Close</button>
              </div>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
              <ItemDetail collectionId={selectedCollectionId ?? ""} item={selectedItem} error={selectedItemError} layout="drawer"
                onBack={onCloseItem} onMap={onViewOnMap} onExplore={onExplore} />
            </div>
          </aside>
        </>
      )}
    </div>
  );
}

// A result-kind chip: what the query found, and how to see only that.
function ScopeChip({ on, onClick, title, children }: {
  on: boolean; onClick: () => void; title?: string; children: React.ReactNode;
}) {
  return (
    <button type="button" onClick={onClick} title={title}
      className={`rounded-full border px-2.5 py-0.5 ${on
        ? "border-primary bg-primary text-primary-foreground"
        : "border-border bg-card text-muted-foreground hover:border-primary hover:text-foreground"}`}>
      {children}
    </button>
  );
}

// ── Facet rail section: a collapsible group of checkbox rows with counts. A lone value isn't a
// filter, so a group under two options hides (same rule the model uses). Long groups collapse to a
// "Show all" so the rail never becomes a wall. ─────────────────────────────────────────────────────
const FACET_HEAD = 8;
function FacetSection({ label, facets, selected, onToggle, defaultOpen = true }: {
  label: string; facets: FacetCount[]; selected: Set<string>; onToggle: (key: string) => void; defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen || selected.size > 0);
  const [all, setAll] = useState(false);
  if (facets.length < 2 && selected.size === 0) return null;   // a lone value isn't a filter, unless it's on
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
                    ? "bg-muted font-medium text-foreground" : "text-foreground hover:bg-hover"}`}>
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
function GeometrySection({ facets, value, onChange, defaultOpen = true }: {
  facets: FacetCount[]; value: FacetSelection["geometry"]; onChange: (v: FacetSelection["geometry"]) => void;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen || value !== "all");
  if (facets.length < 2 && value === "all") return null;
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
                    ? "bg-muted font-medium text-foreground" : "text-foreground hover:bg-hover"}`}>
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

// The result card + list row live in result-card.tsx. This file keeps the Discover shell + the
// facet-rail sections above.
