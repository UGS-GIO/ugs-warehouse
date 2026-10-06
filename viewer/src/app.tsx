import { type ActionItem, loadHeader, setUtahHeaderSettings, type SettingsInput } from "@utahdts/utah-design-system-header";
import { useIsFetching } from "@tanstack/react-query";
import { Link, Outlet, useNavigate, useRouterState, useSearch } from "@tanstack/react-router";
import { createContext, Suspense, useContext, useEffect, useMemo, useRef, useState, useTransition } from "react";
import { type CatalogDoc } from "./discover/search-index";
import utahLogo from "./assets/utah-logo.png";
import { type CollectionSummary, type CoverRef, type ItemRef } from "./catalog/browse";
import { layerCollectionIds } from "./catalog/catalog";
import { type ActiveLayer, type Footprint, layerParam, parseLayerParam, sheetParam } from "./map/map-model";
import { flyTo, queueFocus, setPin } from "./map/camera";
import type { Bounds } from "./map/place-locator";
import { MapSearch } from "./shell/map-search";
import { LegalFooter } from "./shell/legal-footer";
import { SavingNotice } from "./offline/saving-notice";
import { OfflineBadge } from "./offline/offline-notice";
import { type LayerRow } from "./map/layer-list";
import { NavMenu } from "./shell/nav-menu";
import { PreviewMapProvider } from "./map/preview-map";
import { CATALOG_URL, IS_REVIEW, collKeyOf, idOf, childLinks, cogRenderAsset, cubeVariables, itemLinks, hasItemsIndex, parquetAsset, pmtilesLink, rootIndexItems, rasterTilesAsset, type StacDoc, thumbnailAsset, cubeRenderRescale, cubeStepDims, resolveSelection, useDocs, useIndexes, useRootIndex, useStac, useStyleLayersFor, defaultStyleUrl, zarrAsset } from "./stac";
import { useOffline } from "@/offline/store";
import { StacUrlChip } from "./catalog/stac-url-chip";
import { type CubePicks, parseCubeParam, pickedRescale, VAR } from "./zarr/cube-picks";
import { NotifBell } from "./review/notifications-inbox";
import { DataSaverBadge } from "./shell/data-saver-badge";
import { useDataSaver } from "./lib/data-saver";


// collKeyOf + idOf live in the data layer (stac.ts) next to CATALOG_URL. idOf is re-exported here so
// the existing `@/app` importers keep resolving it from this module.
export { idOf } from "./stac";


// `s` = selected data-series codes (DS, OFR, GQ…) — shareable series filter for a collection.
export type View = "catalog" | "map" | "discover" | "guide" | "developers" | "preview" | "review" | "offline" | "settings";
// `satisfies` keeps each value a literal, so `navigate({ to })` typechecks against the generated
// route tree — a computed `/${view}` string would not, which is what the old cast papered over.
const VIEW_PATH = {
  catalog: "/catalog", map: "/map", discover: "/",
  guide: "/guide", developers: "/developers", preview: "/preview",
  review: "/review", offline: "/offline", settings: "/settings",
} satisfies Record<View, string>;
const isView = (v: string): v is View => v in VIEW_PATH;
export type Nav = { view: View; c?: string; i?: string; l?: string[]; s?: string[]; sheet?: string };

// An ItemRef → map ActiveLayer, by asset precedence: vector PMTiles, COG, raster mosaic, datacube.
// null when the item carries none of them — it isn't a layer.
export function toLayer(ref: ItemRef | undefined, cube: CubePicks = {}): ActiveLayer | null {
  if (!ref?.data) return null;
  const id = idOf(ref.href);
  const title = String(ref.data.properties?.title ?? id);
  const pm = pmtilesLink(ref.data);
  if (pm) {
    return {
      id, title,
      pmHref: pm.href,
      tableHref: parquetAsset(ref.data)?.href,
      pmLayer: pm["pmtiles:layers"]?.[0] ?? id,
      bbox: ref.data.bbox,
      styleUrl: defaultStyleUrl(ref.data),
    };
  }
  const cog = cogRenderAsset(ref.data);
  if (cog) return { id, title, cogHref: cog.href, bbox: ref.data.bbox };
  const raster = rasterTilesAsset(ref.data);
  if (raster) return { id, title, rasterPmHref: raster.href, bbox: ref.data.bbox };
  const zarr = zarrAsset(ref.data);
  // No drawable variable → not a layer, rather than a row that can never render.
  const variables = Object.keys(cubeVariables(ref.data));
  if (zarr && variables.length) {
    const stepDims = cubeStepDims(ref.data);
    const variable = cube[VAR] && variables.includes(cube[VAR]) ? cube[VAR] : variables[0];
    return {
      id, title, bbox: ref.data.bbox,
      zarr: {
        href: zarr.href, variable, variables, stepDims,
        selection: resolveSelection(stepDims, cube), rescale: pickedRescale(cube),
        stacRescale: cubeRenderRescale(ref.data, variable),
      },
    };
  }
  return null;
}

// "Does this doc draw as a map layer?" — reuses toLayer's exact gate (the probe href/collId don't
// affect the answer), so the layer list and the Add-to-map button agree with what the map can
// actually render (e.g. a native-CRS COG that toLayer rejects isn't offered as a layer) and can't
// drift from toLayer.
const drawsAsLayer = (data: StacDoc | undefined): boolean =>
  !!data && toLayer({ collId: "", href: "layer/probe", data }) !== null;

// Primary tabs: the desktop tab row + the mobile menu's "Views". Discover leads (it's the star).
const PRIMARY_VIEWS: { id: View; label: string }[] = [
  { id: "discover", label: "Discover" },
  { id: "map", label: "Map" },
  { id: "catalog", label: "Catalog" },
];
// Secondary views — always in the NavMenu overflow (desktop + mobile) so the tab row never overflows.
const OVERFLOW_VIEWS: { id: View; label: string }[] = [
  { id: "guide", label: "Guide" },
  { id: "offline", label: "Offline data" },
  { id: "settings", label: "Settings" },
  ...(IS_REVIEW ? [{ id: "review" as const, label: "Review" }] : []),
];

const tab = (on: boolean) =>
  "cursor-pointer border-b-2 px-2 py-1 text-sm transition-colors "
  + (on ? "border-primary font-medium text-primary" : "border-transparent text-muted-foreground hover:text-foreground");


// The IAP user as a Utah-header action item (top-right of the official banner). Display-only — IAP
// already gated access; the username shows, the full email is the tooltip. Clicking signs out via
// IAP's clear-login-cookie flow.
function userActionItem(email: string): ActionItem {
  const icon = document.createElement("span");
  icon.textContent = "👤";
  icon.title = `Signed in as ${email}`;
  return {
    title: email.split("@")[0],
    showTitle: true,
    icon,
    className: "ugs-user-badge",
    // click → IAP sign-out (clears the login cookie, forces re-auth).
    actionFunction: () => { window.location.href = "/?gcp-iap-mode=CLEAR_LOGIN_COOKIE"; },
  };
}

// Thin top progress bar — visible while any TanStack Query fetch is in flight OR a view switch is
// pending (useTransition). A global "working" signal so a slow load never reads as a frozen app.
const SEARCH_BOX_QUERIES = new Set(["place-suggest", "header-search-index"]);

function FetchBar({ pending }: { pending?: boolean }) {
  // The search box has its own spinner.
  const busy = useIsFetching({ predicate: (q) => !SEARCH_BOX_QUERIES.has(String(q.queryKey[0])) }) > 0 || pending;
  return (
    <div className="pointer-events-none fixed inset-x-0 top-0 z-50 h-0.5 overflow-hidden">
      {busy && <div className="fetch-bar h-full w-full bg-primary" />}
    </div>
  );
}

function useViewState() {
  // Nav state ← URL search (TanStack Router). l/s stay as csv strings in the URL; the override params
  // (catalog, m, ftsdb, …) ride in the same search untouched (see router.tsx validateSearch).
  const sp = useSearch({ from: "__root__" });
  const navigate = useNavigate();
  // The view is the PATH, not a ?view= param — see routes.tsx for why. "/" is Discover.
  const pathname = useRouterState({ select: (st) => st.location.pathname });
  const seg = pathname.replace(/^\/+|\/+$/g, "").split("/")[0];
  const view: View = isView(seg) ? seg : "discover";
  const collectionUrl = sp.c;
  const itemUrl = sp.i;
  const urlLayerIds = parseLayerParam(sp.l);
  const seriesSel = sp.s ? sp.s.split(",").filter(Boolean) : undefined;

  // Navigate by setting the nav search params; override/spike params are preserved. push for user nav
  // (back/forward works via the router), replace for programmatic syncs. The Discover-owned keys
  // (q/collections/category/…) are stripped when leaving Discover so its filters don't linger on
  // another view, and preserved when staying in Discover (open/close a drawer over the filtered set).
  const go = (next: Nav, push = true) => {
    if (next.view !== "map") setPin(null);   // the search's place pin belongs to this map visit
    navigate({
      to: VIEW_PATH[next.view],
      replace: !push,
      search: (prev) => {
        const { view: _v, c: _c, i: _i, l: _l, s: _s, sheet, terrain,
          q, collections, category, types, formats, geometry, sort, layout, density, area,
          ...rest } = prev;  // keep override params (rest); Discover keys re-added only when staying
        // Preview is only ever reached from Discover, so carry the filter state through it → Back
        // restores the filtered result set the user came from.
        const discover = next.view === "discover" || next.view === "preview"
          ? { q, collections, category, types, formats, geometry, sort, layout, density, area } : {};
        return {
          ...rest,
          ...discover,
          sheet: next.view === "map" ? next.sheet ?? sheet : undefined,   // the map's own; it doesn't follow you out
          terrain: next.i && next.i === prev.i ? terrain : undefined,     // a new item opens in 2D
          c: next.c || undefined,
          i: next.i || undefined,
          l: layerParam(next.l),
          s: next.s?.length ? next.s.join(",") : undefined,
        };
      },
    });
  };
  // Tabs: catalog + map share the selection (c/i/l/s); the content views (search/guide) reset
  // it, so the URL stays clean and returning to the catalog doesn't dump you back on an old item.
  // In a transition so switching to a heavy view keeps the current one interactive + flags `pending`.
  const [pending, startTransition] = useTransition();
  const setView = (v: View) =>
    startTransition(() => go(v === "catalog" || v === "map"
      ? { view: v, c: collectionUrl, i: itemUrl, l: layerIds, s: seriesSel }
      : { view: v }));

  // Official State of Utah header — injects the state identity bar + maintained logo above the
  // app (Utah Design System standard). On the review deploy the IAP user shows as a top-right
  // action item (same level as the UGS title/logo), fetched from /whoami. Configured on mount.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const base: SettingsInput = {
        title: "Utah Geological Survey",
        showTitle: true,
        titleUrl: "https://geology.utah.gov",
        // `htmlString` not `imageUrl`: the latter emits an <img> with no alt. Decorative here.
        logo: { htmlString: `<img src="${utahLogo}" alt="" />` },
        mainMenu: false,
        utahId: false,
        size: "SMALL",   // MEDIUM (the default) took about 100 px above every view
      };
      let email = "";
      if (IS_REVIEW) {
        try {
          const r = await fetch("/whoami", { headers: { accept: "application/json" } });
          if (r.ok) email = (await r.json())?.email ?? "";
        } catch { /* public deploy / local: no /whoami */ }
        // Local dev preview: no IAP here, so surface a placeholder to see the badge.
        if (!email && import.meta.env.DEV) email = "dev.user@utah.gov";
      }
      if (cancelled) return;
      setUtahHeaderSettings(email ? { ...base, actionItems: [userActionItem(email)] } : base);
      loadHeader();
    })();
    return () => { cancelled = true; };
  }, []);

  const [search, setSearch] = useState("");
  const [threeD, setThreeD] = useState(false);  // global "3D pubs" discovery filter (loads all items)
  const [browseAll, setBrowseAll] = useState(false);  // flat catalog-wide list, newest-first (loads all items)

  const catalog = useStac(CATALOG_URL);

  // ---- catalog tree (one level of nesting: root → sub-catalog → series collections) ----
  // Counts + titles ride on the child links (warehouse emits ugs:item_count), so the catalog page
  // and the series chooser render from a single fetch each — no per-collection fan-out. A
  // child whose href ends in catalog.json is a nesting sub-catalog (ugs-publications); the
  // rest are leaf collections. (A pre-nesting flat catalog has only leaf collections — this
  // still works: ugs-publications is then just a leaf you open into items.)
  const rootChildren: CollectionSummary[] = childLinks(catalog.data, CATALOG_URL).map((l) => ({
    id: collKeyOf(l.href) ?? l.href, href: l.href, title: l.title, count: l["ugs:item_count"],
    mappable: l["ugs:mappable_count"],
    kind: l.href.endsWith("/catalog.json") ? "catalog" : "collection",
  }));
  const subCats = rootChildren.filter((c) => c.kind === "catalog");
  const subDocs = useDocs(subCats.map((c) => c.href));
  const seriesChildren: CollectionSummary[] = subCats.flatMap((sc, i) =>
    childLinks(subDocs.docs[i]?.data, sc.href).map((l) => ({
      id: collKeyOf(l.href) ?? l.href, href: l.href, title: l.title,
      count: l["ugs:item_count"], mappable: l["ugs:mappable_count"], kind: "collection", parentId: sc.id,
    })));
  const leafColls = [...rootChildren.filter((c) => c.kind === "collection"), ...seriesChildren];
  const layerCollIds = layerCollectionIds(rootChildren, seriesChildren);

  const collectionId = collKeyOf(collectionUrl);  // idempotent on a bare key; also handles legacy full-url `c`
  const subCat = subCats.find((c) => c.id === collectionId);
  const leafColl = leafColls.find((c) => c.id === collectionId);
  // Cards for the current browse level: none at a leaf (items show); a sub-catalog's series; else root.
  const cards = leafColl ? [] : subCat ? seriesChildren.filter((c) => c.parentId === subCat.id) : rootChildren;

  // ---- items for the open leaf, index-driven + lazy (one items.json fetch) ----
  // Loaded for the open leaf, or every leaf while a global search runs. Graceful fallback:
  // a leaf whose items.json is missing (pre-index catalog) fetches its collection.json for
  // item links, then those items — scoped, never a catalog-wide fan-out.
  // Both search-all and the global 3D filter need every collection's items loaded.
  const searching = !collectionId && (search.trim().length > 0 || threeD || browseAll);
  const wantColls = leafColl ? [leafColl] : searching ? leafColls : [];
  const idx = useIndexes(wantColls.map((c) => ({ id: c.id, href: c.href })));

  const itemHrefIn = (collHref: string, id: string) =>
    collHref.replace(/collection\.json(\?.*)?$/, `${encodeURIComponent(id)}/${encodeURIComponent(id)}.json`);

  // ---- cover strips on the collection cards (latest covers, newest first) ----
  // Only while cards show (catalog root / a sub-catalog) — not inside a leaf's item list. Fetches
  // every leaf's items.json so a sub-catalog card (Publications) can aggregate latest-across-series.
  // Shares the ["index", href] cache with the item-list fetch above, so overlapping leaves load once.
  // Decided from the URL, not from `leafColl`: that is unknown until the sub-catalog loads, and in
  // that gap a layer page fetched every collection's index (all 38 publication series, 400 KB+).
  const showsCards = view === "catalog" && !itemUrl && (!collectionId || subCats.some((c) => c.id === collectionId));
  // Data saver shows no covers (catalog/browse.tsx), so it fetches none of the indexes behind them.
  const saver = useDataSaver();
  const coverColls = showsCards && !saver ? leafColls : [];
  const coverIdx = useIndexes(coverColls.map((c) => ({ id: c.id, href: c.href })));
  const coversByColl = useMemo(() => {
    const out: Record<string, CoverRef[]> = {};
    for (const r of coverIdx) {
      const date = (d: StacDoc) => String((d.properties as Record<string, unknown> | undefined)?.datetime ?? "");
      out[r.id] = (r.index?.items ?? [])
        .map((d) => ({ d, th: thumbnailAsset(d) }))
        .filter((x) => x.th)
        .sort((a, b) => date(b.d).localeCompare(date(a.d)))
        .slice(0, 5)
        .map(({ d, th }) => ({
          href: itemHrefIn(r.href, String(d.id)), thumb: th!.href,
          title: String((d.properties as Record<string, unknown> | undefined)?.title ?? d.id ?? ""),
          date: date(d),
        }));
    }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [coverIdx.map((r) => `${r.id}:${r.index?.items?.length ?? 0}`).join("|")]);

  // Every catalog item flattened for the Search view (title + id + keywords + topic), so search
  // covers all pubs + map layers, not just Survey Notes. Same indexes the cover strips load.
  const catalogDocs = useMemo<CatalogDoc[]>(() =>
    coverIdx.flatMap((r) => (r.index?.items ?? []).map((d) => {
      const p = (d.properties ?? {}) as Record<string, unknown>;
      return {
        id: `${r.id}/${d.id}`, collId: r.id, itemId: String(d.id),
        title: String(p.title ?? d.id),
        keywords: String(p.keywords ?? ""),
        meta: [p["ugs:series"], p["ugs:topic"], p["ugs:pub_type"]].filter(Boolean).join(" · "),
      };
    })),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [coverIdx.map((r) => `${r.id}:${r.index?.items?.length ?? 0}`).join("|")]);

  // Attach covers to each card: a leaf uses its own; a sub-catalog (Publications) merges its series'
  // covers and re-sorts newest-first across all of them.
  const cardsWithCovers = useMemo<CollectionSummary[]>(() => cards.map((c) => {
    const covers = c.kind === "catalog"
      ? seriesChildren.filter((s) => s.parentId === c.id).flatMap((s) => coversByColl[s.id] ?? [])
          .sort((a, b) => (b.date ?? "").localeCompare(a.date ?? "")).slice(0, 5)
      : coversByColl[c.id] ?? [];
    return covers.length ? { ...c, covers } : c;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [cards, coversByColl]);

  const fbColls = wantColls.filter((_, i) => idx[i]?.missing);
  const fbCollDocs = useDocs(fbColls.map((c) => c.href));
  const fallbackRefs = fbColls.flatMap((c, i) =>
    itemLinks(fbCollDocs.docs[i]?.data, c.href).map((l) => ({ collId: c.id, href: l.href })));
  const fbDocs = useDocs(fallbackRefs.map((r) => r.href));

  const allItems: ItemRef[] = [
    ...idx.flatMap((r) => (r.index?.items ?? []).map((d) => ({
      collId: r.id, href: itemHrefIn(r.href, String(d.id)), data: d,
    }))),
    ...fallbackRefs.map((r, i) => ({ ...r, data: fbDocs.docs[i]?.data })),
  ];
  const itemsLoading = idx.some((r) => r.isLoading) || fbDocs.isLoading;

  // The Map and Discover views load every leaf collection's index (all items → the map, the facets
  // and the category index). Offline data needs it too, to name what is saved; it is the same set.
  const mapColls = view === "map" || view === "discover" || view === "offline"
    ? leafColls : [];
  // The root index covers this catalog; only federated collections load their own index.
  const root = useRootIndex(mapColls.length > 0);
  const idxColls = root.isLoading ? [] : mapColls.filter((c) => !(root.data && hasItemsIndex(c.href)));
  const mapIdx = useIndexes(idxColls.map((c) => ({ id: c.id, href: c.href })));
  // Same collection.json → item-links fallback the browse list uses. Without it a federated
  // catalog contributes no layers at all: it publishes no items.json, so the index is empty and
  // its datacubes never reach the layer list. Bounded — only index-less collections take this path.
  const mapFbColls = idxColls.filter((_, i) => mapIdx[i]?.missing);
  const mapFbCollDocs = useDocs(mapFbColls.map((c) => c.href));
  const mapFbRefs = mapFbColls.flatMap((c, i) =>
    itemLinks(mapFbCollDocs.docs[i]?.data, c.href).map((l) => ({ collId: c.id, href: l.href })));
  const mapFbDocs = useDocs(mapFbRefs.map((r) => r.href));
  const mapItems: ItemRef[] = [
    ...(root.data ? rootIndexItems(root.data) : []),
    ...mapIdx.flatMap((r) => (r.index?.items ?? []).map((d) => ({
      collId: r.id, href: itemHrefIn(r.href, String(d.id)), data: d,
    }))),
    ...mapFbRefs.map((r, i) => ({ ...r, data: mapFbDocs.docs[i]?.data })),
  ];
  // How much of the map's data has loaded. The fallback count is what federated layers depend on:
  // they arrive only that way, and always after the indexes.
  const mapLoadKey = `root:${root.data?.count ?? 0}|` + mapIdx.map((r) => `${r.id}:${r.index?.items?.length ?? 0}`).join("|")
    + `|fb:${mapFbDocs.docs.filter((d) => d?.data).length}`;
  // Still streaming, so Discover's category index doesn't show counts that are still climbing.
  const mapItemsLoading = mapColls.length > 0
    && (root.isLoading || mapIdx.some((r) => r.isLoading) || mapFbDocs.isLoading);
  // Opened with no layers chosen, the Map shows the ones saved for offline, so a layer saved from
  // its page is on when you come back without a connection. Choosing layers (or none) takes over.
  const offline = useOffline();
  const savedUrls = new Set(offline.files.map((f) => f.url));
  const savedLayerIds = view === "map" && !urlLayerIds && savedUrls.size
    ? mapItems.flatMap((r) => {
        const l = toLayer(r);
        const href = l?.pmHref ?? l?.rasterPmHref ?? l?.cogHref;
        return l && href && savedUrls.has(href) ? [l.id] : [];
      })
    : [];
  const layerIds = urlLayerIds ?? (savedLayerIds.length ? savedLayerIds : undefined);
  // Layer collections first — the serving topics are what the map is for; pub plates come after.
  const collTitle = (id: string) => leafColls.find((c) => c.id === id)?.title ?? id;
  const layerRows: LayerRow[] = useMemo(() => mapItems
    .filter((r) => drawsAsLayer(r.data))
    // A datacube is a data layer whatever catalog it came from — the sub-catalog allowlist only
    // knows our own ids, so a federated cube would otherwise file under publication plates.
    .map((r) => ({ id: idOf(r.href), href: r.href, title: String(r.data?.properties?.title ?? idOf(r.href)),
                   group: collTitle(r.collId), layer: layerCollIds.includes(r.collId) || !!zarrAsset(r.data) }))
    .sort((a, b) => Number(b.layer) - Number(a.layer)
                    || a.group.localeCompare(b.group) || a.title.localeCompare(b.title)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [mapLoadKey]);

  // i in the URL may be a short id (?i=GQ-1560) or a full STAC URL (older links). Resolve to
  // an absolute href: construct from the open leaf, else look it up among loaded items.
  const isUrl = (s?: string) => Boolean(s) && /^https?:\/\//.test(s as string);

  // Deep-link straight into a sub-catalog that publishes a rollup index — `?c=ugs-serving-topics&i=<id>`.
  // The item lives in a child collection (its mart schema), which the index entry carries, so the
  // href is resolvable without opening that child first. Keeps links minted before the per-schema
  // split working, and is the path the review dashboard's "open item" takes.
  const rollupWanted = Boolean(subCat && !leafColl && itemUrl && !isUrl(itemUrl));
  const rollupIdx = useIndexes(rollupWanted && subCat ? [{ id: subCat.id, href: subCat.href }] : []);
  const rollupHit = rollupIdx[0]?.index?.items?.find((d) => String(d.id) === itemUrl);
  const rollupSchema = (rollupHit?.properties as Record<string, unknown> | undefined)?.["ugs:dbt_schema"];
  const rollupHref = subCat && rollupHit && rollupSchema
    ? itemHrefIn(subCat.href.replace(/catalog\.json(\?.*)?$/, `${encodeURIComponent(String(rollupSchema))}/collection.json`),
                 String(rollupHit.id))
    : undefined;

  const itemHref = isUrl(itemUrl)
    ? itemUrl
    : (leafColl && itemUrl ? itemHrefIn(leafColl.href, itemUrl) : undefined)
      ?? rollupHref
      ?? allItems.find((r) => idOf(r.href) === itemUrl)?.href;
  const item = useStac(itemHref);

  // Open a collection fresh (series filter is per-collection → cleared). Item open / layer
  // toggle / back-to-items keep the active series filter so it survives drilling in + out.
  const revealInfo = useRef<(() => void) | null>(null);   // MapShell hands back "show the detail"
  const openCollection = (href: string) => go({ view, c: collKeyOf(href) });
  // Derive the collection from the item href rather than the ambient collectionUrl — search-all results
  // span collections, so the ambient one is wrong (or absent). Mirrors openCover.
  const openItem = (href: string) => {
    revealInfo.current?.();   // picking on the map raises its detail — the sheet/dock, not a nav
    go({ view, c: collKeyOf(href), i: idOf(href), l: layerIds, s: seriesSel });
  };
  // Open an item straight from a catalog cover strip (no collection open first): derive the leaf
  // collection key from the item href so it resolves + the URL stays tidy.
  const openCover = (href: string) => go({ view, c: collKeyOf(href), i: idOf(href) });
  // The full item page. Below lg this replaces the Discover drawer.
  const openItemPage = (href: string) => go({ view: "catalog", c: collKeyOf(href), i: idOf(href) });
  // The header search hands a query to Discover, clearing stale Discover keys. `to` is REQUIRED: a
  // view is a path, so navigating with search alone would stay put.
  const openDiscoverSearch = (opts: { q?: string; category?: string }) =>
    navigate({
      to: VIEW_PATH.discover,
      search: (prev) => {
        const { view: _v, c: _c, i: _i, l: _l, s: _s,
          q: _q, collections: _co, category: _ca, types: _ty, formats: _fo, geometry: _ge,
          sort: _so, layout: _la, density: _de, area: _ar, sheet: _sh, ...rest } = prev;
        return { ...rest, q: opts.q || undefined, category: opts.category || undefined };
      },
    });
  const setSeries = (codes: string[]) => go({ view, c: collectionUrl, i: itemUrl, l: layerIds, s: codes });
  // Turning a layer ON puts it at the FRONT of the draw order (tray top row = drawn on top); OFF
  // drops it in place. Front-on-add keeps "add to map" coherent with top=front stacking — a newly
  // added layer lands on top, not hidden behind what's already there. The `new Set` also dedupes,
  // so a duplicate id from a hand-edited ?l= self-heals instead of double-mounting a source.
  const toggleLayer = (id: string) => {
    const cur = layerIds ?? [];
    const next = cur.includes(id) ? cur.filter((x) => x !== id) : [id, ...cur];
    go({ view, c: collectionUrl, i: itemUrl, l: [...new Set(next)], s: seriesSel });
  };
  // A whole group at once (a series, a mart schema, or the active set) in ONE navigation, not N:
  // toggleLayer reads `layerIds` from the URL, so a loop over it would drop all but the last id.
  // Adds land at the front too, keeping the batch's own order; removes just drop out. `new Set`
  // dedupes (a self-duplicated batch or a hand-edited ?l= can't double-mount a source).
  const toggleLayers = (ids: string[], on: boolean) => {
    const cur = layerIds ?? [];
    let next: string[];
    if (on) next = [...ids.filter((id) => !cur.includes(id)), ...cur];
    else { const rm = new Set(ids); next = cur.filter((id) => !rm.has(id)); }
    go({ view, c: collectionUrl, i: itemUrl, l: [...new Set(next)], s: seriesSel });
  };
  // "+ Add to map" from a discovery/browse card or the item detail: accumulate into ?l= (never
  // replace), so pulling in a layer while browsing keeps the ones already drawn. `isActive` reads
  // the explicit set the add/remove controls manage; the implicit open-item fallback stays a
  // map-view rendering nicety, unaffected here.
  const addLayer = (id: string) => toggleLayers([id], true);
  // Header search picks. Both land on the map: a place flies there; an item opens its detail, joins
  // the layers when it draws as one, and the map zooms to it rather than to every layer.
  const searchIsLayer = (r: ItemRef) => layerCollIds.includes(r.collId) || !!zarrAsset(r.data);
  const pickPlace = (b: Bounds, label: string) => {
    setPin({ lng: (b[0] + b[2]) / 2, lat: (b[1] + b[3]) / 2, label });
    if (view === "map") flyTo(b);
    else { queueFocus(b); go({ view: "map", l: layerIds, s: seriesSel }); }
  };
  const pickSearchItem = ({ href, bbox }: { href: string; bbox?: Bounds }) => {
    setPin(null);   // an item marks itself with its footprint
    const id = idOf(href);
    const cur = layerIds ?? [];
    const adds = !cur.includes(id) && drawsAsLayer(mapItems.find((r) => r.href === href)?.data);
    if (bbox) { if (view === "map" && !adds) flyTo(bbox); else queueFocus(bbox); }
    revealInfo.current?.();
    go({ view: "map", c: collKeyOf(href), i: id, l: adds ? [id, ...cur] : layerIds, s: seriesSel,
         sheet: sheetParam({ tab: "info", detent: 1 }) });
  };
  const removeLayer = (id: string) => toggleLayers([id], false);
  const isActive = (id: string) => (layerIds ?? []).includes(id);
  // Commit a dragged draw-order back to ?l= (the whole active set stays shareable in the URL).
  const setLayerOrder = (ids: string[]) => go({ view, c: collectionUrl, i: itemUrl, l: ids, s: seriesSel });

  // Breadcrumb trail: Catalog [ / Publications] [ / DS] [ / item]. Each crumb but the last
  // is clickable. parentOfLeaf is the sub-catalog a series collection hangs under (if any).
  const parentOfLeaf = leafColl?.parentId ? subCats.find((s) => s.id === leafColl.parentId) : undefined;
  const crumbs: { label: string; onClick?: () => void }[] = [
    { label: "Catalog", onClick: collectionId || itemUrl ? () => go({ view }) : undefined },
  ];
  if (subCat) crumbs.push({ label: subCat.title ?? subCat.id });
  if (parentOfLeaf) crumbs.push({ label: parentOfLeaf.title ?? parentOfLeaf.id, onClick: () => go({ view, c: parentOfLeaf.id }) });
  if (leafColl) crumbs.push({ label: leafColl.title ?? leafColl.id, onClick: itemUrl ? () => go({ view, c: leafColl.id, s: seriesSel }) : undefined });
  if (itemUrl) crumbs.push({ label: String(itemUrl) });

  // Active map layers: the toggled set, else fall back to the detail item (so a plain
  // ?c=&i= link still shows its layer). Resolved against fetched item data (for PMTiles).
  // The compact index records omit `renders`, so prefer the FULL detail item for the open id
  // (it carries the bound style_url) — otherwise the map can't style the selected layer.
  const byId = new Map([...mapItems, ...allItems].map((r) => [idOf(r.href), r]));
  if (item.data && itemUrl) byId.set(idOf(itemUrl), { collId: collectionId ?? "", href: itemHref ?? itemUrl, data: item.data });
  const idsForMap = layerIds ?? (itemUrl ? [idOf(itemUrl)] : []);
  const cubePicks = parseCubeParam(sp.cube);
  const activeLayers = idsForMap.map((id) => toLayer(byId.get(id), cubePicks[id]))
    .filter((l): l is ActiveLayer => l !== null);

  // Coverage overlay data: every loaded item that has a bbox → a footprint rectangle. Lets the map
  // show WHAT IS MAPPED WHERE across the open collection, including items with no COG/PMTiles asset
  // (most of them) that otherwise draw nothing. Scoped to the loaded collection's index (cheap — the
  // bboxes are already fetched); a catalog-wide overlay would need loading every collection's index.
  const footprints: Footprint[] = allItems
    .map((r) => ({ href: r.href, id: idOf(r.href),
                   title: String(r.data?.properties?.title ?? idOf(r.href)), bbox: r.data?.bbox }))
    .filter((f): f is Footprint => Array.isArray(f.bbox) && f.bbox.length >= 4);

  // Map view = locked viewport (the map fills the screen, panels scroll internally).
  // Catalog/detail = a document → the page scrolls naturally, header sticks. (No more
  // scroll-box stuck in the middle of an item page.)
  const mapView = view === "map";
  // The Map, Discover and Preview views LOCK the viewport (a full-height split/shell whose inner panes
  // scroll) — unlike the document views (catalog/guide/developers/…), which scroll the page
  // under a sticky header.
  const lockedView = mapView || view === "discover" || view === "preview";
  // Same cached queries the map itself reads (TanStack dedupes by key) — the legend needs the bound
  // style layers, and the drawer renders outside the map component.
  const styleCache = useStyleLayersFor(activeLayers.map((l) => ({ id: l.id, styleUrl: l.styleUrl })));
  // "Is this item a resolvable map layer?" — the gate the Add-to-map button self-checks. `layerRows`
  // only covers the map/discover set (mapColls), so on the item page (catalog view, where
  // mapColls is empty) it's empty. Also treat the OPEN item as a layer when its own loaded doc draws
  // as one — otherwise the item-detail Add-to-map button is permanently hidden there.
  const isLayerId = (id: string) =>
    layerRows.some((r) => r.id === id) || (!!itemUrl && idOf(itemUrl) === id && drawsAsLayer(item.data));
  return {
    go, view, setView, catalog, lockedView, pending, mapView,
    catalogDocs, mapItems, mapLoadKey, mapItemsLoading,
    searchIsLayer, pickPlace, pickSearchItem,
    openItem, openItemPage, openDiscoverSearch, openCollection, openCover,
    itemUrl, item, collectionId, collectionUrl, layerIds, seriesSel,
    rootChildren, cardsWithCovers, allItems, itemsLoading, leafColl, crumbs,
    search, setSearch, threeD, setThreeD, browseAll, setBrowseAll,
    layerCollIds, setSeries,
    revealInfo, activeLayers, footprints, layerRows, idsForMap, toggleLayer, toggleLayers, styleCache,
    addLayer, removeLayer, isActive, isLayerId, setLayerOrder,
  };
}

/** Everything the view routes need. Inferred from useViewState so the two cannot drift. */
export type ViewCtx = ReturnType<typeof useViewState>;
const ViewContext = createContext<ViewCtx | null>(null);

export const useViewCtx = (): ViewCtx => {
  const ctx = useContext(ViewContext);
  if (!ctx) throw new Error("useViewCtx must be used inside AppLayout");
  return ctx;
};

/** The map search, wired to the view state. */
export function MapSearchFor({ state, className }: { state: ViewCtx; className?: string }) {
  return <MapSearch items={state.mapItems} loadKey={state.mapLoadKey} isLayer={state.searchIsLayer}
    onPlace={state.pickPlace} onItem={state.pickSearchItem}
    onSearchAll={(q) => state.openDiscoverSearch({ q })} className={className} />;
}

/** Root layout route: owns the data + shell, renders the matched view through <Outlet />. */
export function AppLayout() {
  const state = useViewState();
  const { view, setView, catalog, lockedView, pending } = state;
  return (
    // One persistent preview map lives in this provider (mounted once, above the view/list/item
    // boundary) so item navigation swaps sources instead of churning WebGL contexts. See PreviewMap.
    <PreviewMapProvider>
    <div className={lockedView
      ? "grid h-full grid-cols-1 grid-rows-[auto_1fr] overflow-hidden bg-background text-sm text-foreground"
      : "flex h-full flex-col overflow-y-auto overflow-x-hidden bg-background text-sm text-foreground"}>
      <FetchBar pending={pending} />
      <header className={`flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-border bg-background px-3 py-2 sm:px-4 ${lockedView ? "" : "sticky top-0 z-20"}`}>
        <Link to="/" title="Home"
          className="flex items-center gap-2 whitespace-nowrap hover:opacity-80">
          {/* The emblem shows only where index.css hides the state band (phones, short screens). Where
              the band shows it already carries the beehive, and a second copy read as a duplicate. */}
          <span className="app-emblem shrink-0 items-center gap-2">
            <img src={utahLogo} alt="Utah Geological Survey" className="h-5 w-auto dark:brightness-0 dark:invert" />
            <span aria-hidden className="hidden text-sm font-semibold text-muted-foreground md:inline">Utah Geological Survey</span>
            <span aria-hidden className="mr-1 hidden h-4 w-px bg-border md:inline" />
          </span>
          <strong className="font-display text-xl tracking-tight">UGS Warehouse</strong>
        </Link>
        {/* Beside the name, not in a hero — the URL applies to every view. */}
        <StacUrlChip url={CATALOG_URL} />
        <div className="ml-auto flex items-center gap-1">
          {/* The same views twice, but only one is ever rendered: tabs where they fit, hamburger
              below md — five tabs and a phone don't share a row. */}
          <div className="hidden gap-1 md:flex">
            {PRIMARY_VIEWS.map((v) => (
              <button key={v.id} type="button" aria-current={view === v.id ? "page" : undefined}
                className={tab(view === v.id)} onClick={() => setView(v.id)}>{v.label}</button>
            ))}
          </div>
          <DataSaverBadge />
          <OfflineBadge />
          {IS_REVIEW && <NotifBell onClick={() => setView("review")} />}
          {/* Always mounted: it carries the theme picker + the overflow views, and below md the
              primary tabs as well. */}
          <NavMenu current={view} catalogUrl={CATALOG_URL}
            pages={PRIMARY_VIEWS.map((v) => ({ id: v.id, label: v.label, onSelect: () => setView(v.id) }))}
            overflow={OVERFLOW_VIEWS.map((v) => ({ id: v.id, label: v.label, onSelect: () => setView(v.id) }))} />
        </div>
      </header>

      {catalog.error && <p className="p-4 text-destructive">{String(catalog.error)}</p>}

      <Suspense fallback={<div className="flex items-center justify-center p-16 text-sm text-muted-foreground">Loading…</div>}>
      <ViewContext.Provider value={state}>
        <Outlet />
        <SavingNotice />
      </ViewContext.Provider>
      </Suspense>
      {!lockedView && <LegalFooter className="mt-auto" catalogUrl={CATALOG_URL} />}
    </div>
    </PreviewMapProvider>
  );
}
