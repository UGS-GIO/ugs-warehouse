import { type ActionItem, loadHeader, setUtahHeaderSettings, type SettingsInput } from "@utahdts/utah-design-system-header";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";
import { Architecture } from "./Architecture";
import { ArticleSearch, type CatalogDoc } from "./search";
import { Guide } from "./Guide";
import utahLogo from "./assets/utah-logo.png";
import { Browse, type CollectionSummary, type CoverRef, type ItemRef } from "./Browse";
import { type ActiveLayer, colorFor, type Footprint, ItemMap } from "./Map";
import { CATALOG_URL, IS_REVIEW, childLinks, cogAsset, itemLinks, pmtilesLink, rasterTilesAsset, type StacDoc, thumbnailAsset, useDocs, useIndexes, useStac, defaultStyleUrl } from "./stac";
import { useTheme } from "./theme";
import { DiffPanel } from "./DiffPanel";
import { CommentsPanel } from "./CommentsPanel";
import { NotifBell } from "./NotificationsInbox";
import { ReviewDashboard } from "./ReviewDashboard";

const collIdOf = (url?: string) => url?.split("/").slice(-2)[0];
const idOf = (href: string) => href.split("/").slice(-2)[0]; // item id = its folder name

// Viewer root URL (the served path, no search params) — used as the logo's <a href> so
// modifier/middle-click opens the catalog in a new tab. Uses the document's own pathname (…/index.html
// in prod) to match router.tsx's basepath, so the link points at a real object (no NoSuchKey).
const ROOT_HREF = location.pathname || "/";

// `s` = selected data-series codes (DS, OFR, GQ…) — shareable series filter for a collection.
type View = "catalog" | "map" | "arch" | "guide" | "search" | "review";
type Nav = { view: View; c?: string; i?: string; l?: string[]; s?: string[] };

// An ItemRef → map ActiveLayer. Prefer PMTiles (vector); else fall back to a COG (raster) so
// publication/raster items render on the overlay too. null if it has neither.
function toLayer(ref: ItemRef | undefined): ActiveLayer | null {
  if (!ref?.data) return null;
  const id = idOf(ref.href);
  const title = String(ref.data.properties?.title ?? id);
  const pm = pmtilesLink(ref.data);
  if (pm) {
    return {
      id, title,
      pmHref: pm.href,
      pmLayer: pm["pmtiles:layers"]?.[0] ?? id,
      bbox: ref.data.bbox,
      styleUrl: defaultStyleUrl(ref.data),
    };
  }
  const cog = cogAsset(ref.data);
  if (cog) return { id, title, cogHref: cog.href, bbox: ref.data.bbox };
  const raster = rasterTilesAsset(ref.data);
  if (raster) return { id, title, rasterPmHref: raster.href, bbox: ref.data.bbox };
  return null;
}

const tab = (on: boolean) =>
  `cursor-pointer rounded-md border px-3 py-1.5 text-[13px] ${on ? "border-primary bg-primary text-primary-foreground" : "border-border bg-card text-foreground hover:bg-accent"}`;
const asset = "mr-1.5 mt-0.5 inline-block rounded bg-primary px-2 py-1 text-xs text-primary-foreground no-underline hover:opacity-90";
const row = "mb-1.5 cursor-pointer rounded-md border border-border bg-card px-2 py-1.5 break-all hover:border-primary";

function MapDetail({ item, loading }: { item?: StacDoc; loading: boolean }) {
  if (loading) return <em>Loading item…</em>;
  if (!item) return <em className="text-muted-foreground">Pick an item to see detail, footprint, and assets.</em>;
  const p = item.properties ?? {};
  // Review deploy only: offer a diff of this _review item against its live _current counterpart.
  const isReview = IS_REVIEW;
  const geoparquet = Object.entries(item.assets ?? {})
    .find(([k, a]) => /parquet/i.test(String(a.type ?? "")) || /parquet|geoparquet/i.test(k))?.[1]?.href;
  return (
    <>
      <h2 className="mb-1.5 text-base font-semibold">{String(p.title ?? item.id ?? "")}</h2>
      <div>
        {Object.entries(item.assets ?? {}).map(([k, a]) => (
          <a key={k} className={asset} href={a.href} target="_blank" rel="noopener">{a.title ?? k}</a>
        ))}
      </div>
      {isReview && geoparquet && (
        <DiffPanel stem={String(item.id ?? "")} reviewParquetUrl={geoparquet} />
      )}
      {isReview && item.id && <CommentsPanel itemId={String(item.id)} />}
      <table className="mt-2 w-full border-collapse text-sm">
        <tbody>
          {Object.entries(p).filter(([, v]) => v !== null && v !== "").map(([k, v]) => (
            <tr key={k}>
              <td className="whitespace-nowrap px-2 py-0.5 align-top text-muted-foreground">{k}</td>
              <td className="border-b border-border px-2 py-0.5">{String(v)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

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

function ThemeToggle() {
  const [theme, toggle] = useTheme();
  return (
    <button onClick={toggle} aria-label="Toggle theme"
      className="rounded-md border border-border bg-card px-2 py-1.5 text-[13px] text-foreground hover:bg-accent">
      {theme === "dark" ? "☀" : "☾"}
    </button>
  );
}

export function App() {
  // Nav state ← URL search (TanStack Router). l/s stay as csv strings in the URL; the override params
  // (catalog, m, ftsdb, …) ride in the same search untouched (see router.tsx validateSearch).
  const sp = useSearch({ strict: false }) as { view?: View; c?: string; i?: string; l?: string; s?: string };
  // Loose navigate signature — the router types it strictly against the search schema, but we manage
  // these params dynamically (and pass override params through), so a permissive reducer is intended.
  const navigate = useNavigate() as unknown as (opts: {
    replace?: boolean; search: (prev: Record<string, unknown>) => Record<string, unknown>;
  }) => void;
  const view: View = sp.view ?? "catalog";
  const collectionUrl = sp.c;
  const itemUrl = sp.i;
  const layerIds = sp.l ? sp.l.split(",").filter(Boolean) : undefined;
  const seriesSel = sp.s ? sp.s.split(",").filter(Boolean) : undefined;

  // Navigate by setting the nav search params; everything else in the search is preserved. push for
  // user nav (back/forward works via the router), replace for programmatic syncs.
  const go = (next: Nav, push = true) => {
    navigate({
      replace: !push,
      search: (prev) => {
        const { view: _v, c: _c, i: _i, l: _l, s: _s, ...rest } = prev;  // keep override params
        return {
          ...rest,
          view: next.view === "catalog" ? undefined : next.view,
          c: next.c || undefined,
          i: next.i || undefined,
          l: next.l?.length ? next.l.join(",") : undefined,
          s: next.s?.length ? next.s.join(",") : undefined,
        };
      },
    });
  };
  // Tabs: catalog + map share the selection (c/i/l/s); the content views (search/arch/guide) reset
  // it, so the URL stays clean and returning to the catalog doesn't dump you back on an old item.
  const setView = (v: View) =>
    go(v === "catalog" || v === "map"
      ? { view: v, c: collectionUrl, i: itemUrl, l: layerIds, s: seriesSel }
      : { view: v });

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
        logo: { imageUrl: utahLogo },   // generic State of Utah emblem (until UGS has its own brand)
        mainMenu: false,
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

  const catalog = useStac(CATALOG_URL);

  // ---- catalog tree (one level of nesting: root → sub-catalog → series collections) ----
  // Counts + titles ride on the child links (warehouse emits ugs:item_count), so the landing
  // and the series chooser render from a single fetch each — no per-collection fan-out. A
  // child whose href ends in catalog.json is a nesting sub-catalog (ugs-publications); the
  // rest are leaf collections. (A pre-nesting flat catalog has only leaf collections — this
  // still works: ugs-publications is then just a leaf you open into items.)
  const rootChildren: CollectionSummary[] = childLinks(catalog.data, CATALOG_URL).map((l) => ({
    id: collIdOf(l.href) ?? l.href, href: l.href, title: l.title, count: l["ugs:item_count"],
    mappable: l["ugs:mappable_count"],
    kind: l.href.endsWith("/catalog.json") ? "catalog" : "collection",
  }));
  const subCats = rootChildren.filter((c) => c.kind === "catalog");
  const subDocs = useDocs(subCats.map((c) => c.href));
  const seriesChildren: CollectionSummary[] = subCats.flatMap((sc, i) =>
    childLinks(subDocs.docs[i]?.data, sc.href).map((l) => ({
      id: collIdOf(l.href) ?? l.href, href: l.href, title: l.title,
      count: l["ugs:item_count"], mappable: l["ugs:mappable_count"], kind: "collection", parentId: sc.id,
    })));
  const leafColls = [...rootChildren.filter((c) => c.kind === "collection"), ...seriesChildren];

  const collectionId = collIdOf(collectionUrl);
  const subCat = subCats.find((c) => c.id === collectionId);
  const leafColl = leafColls.find((c) => c.id === collectionId);
  // Cards for the current browse level: none at a leaf (items show); a sub-catalog's series; else root.
  const cards = leafColl ? [] : subCat ? seriesChildren.filter((c) => c.parentId === subCat.id) : rootChildren;

  // ---- items for the open leaf, index-driven + lazy (one items.json fetch) ----
  // Loaded for the open leaf, or every leaf while a global search runs. Graceful fallback:
  // a leaf whose items.json is missing (pre-index catalog) fetches its collection.json for
  // item links, then those items — scoped, never a catalog-wide fan-out.
  // Both search-all and the global 3D filter need every collection's items loaded.
  const searching = !collectionId && (search.trim().length > 0 || threeD);
  const wantColls = leafColl ? [leafColl] : searching ? leafColls : [];
  const idx = useIndexes(wantColls.map((c) => ({ id: c.id, href: c.href })));

  const itemHrefIn = (collHref: string, id: string) =>
    collHref.replace(/collection\.json(\?.*)?$/, `${encodeURIComponent(id)}/${encodeURIComponent(id)}.json`);

  // ---- cover strips on the collection cards (latest covers, newest first) ----
  // Only while cards show (catalog root / a sub-catalog) — not inside a leaf's item list. Fetches
  // every leaf's items.json so a sub-catalog card (Publications) can aggregate latest-across-series.
  // Shares the ["index", href] cache with the item-list fetch above, so overlapping leaves load once.
  const coverColls = leafColl ? [] : leafColls;
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

  // i in the URL may be a short id (?i=GQ-1560) or a full STAC URL (older links). Resolve to
  // an absolute href: construct from the open leaf, else look it up among loaded items.
  const isUrl = (s?: string) => Boolean(s) && /^https?:\/\//.test(s as string);
  const itemHref = isUrl(itemUrl)
    ? itemUrl
    : (leafColl && itemUrl ? itemHrefIn(leafColl.href, itemUrl) : undefined)
      ?? allItems.find((r) => idOf(r.href) === itemUrl)?.href;
  const item = useStac(itemHref);

  // Open a collection fresh (series filter is per-collection → cleared). Item open / layer
  // toggle / back-to-items keep the active series filter so it survives drilling in + out.
  const openCollection = (href: string) => go({ view, c: collIdOf(href) });
  // Derive the collection from the item href (…/<collection>/<id>/<id>.json) rather than the ambient
  // collectionUrl — search-all results span collections, so the ambient one is wrong (or absent) and
  // the item detail (gated on a resolved leaf collection) never shows. Mirrors openCover.
  const openItem = (href: string) =>
    go({ view, c: href.split("/").slice(-3)[0], i: idOf(href), l: layerIds, s: seriesSel });
  // Open an item straight from a catalog cover strip (no collection open first): derive the leaf
  // collection id from the item href (…/<collection>/<id>/<id>.json) so it resolves + the URL stays tidy.
  const openCover = (href: string) => go({ view, c: href.split("/").slice(-3)[0], i: idOf(href) });
  const setSeries = (codes: string[]) => go({ view, c: collectionUrl, i: itemUrl, l: layerIds, s: codes });
  const toggleLayer = (id: string) => {
    const set = new Set(layerIds ?? []);
    if (set.has(id)) set.delete(id); else set.add(id);
    go({ view, c: collectionUrl, i: itemUrl, l: [...set], s: seriesSel });
  };

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
  const byId = new Map(allItems.map((r) => [idOf(r.href), r]));
  if (item.data && itemUrl) byId.set(idOf(itemUrl), { collId: collectionId ?? "", href: itemHref ?? itemUrl, data: item.data });
  const idsForMap = layerIds?.length ? layerIds : itemUrl ? [idOf(itemUrl)] : [];
  const activeLayers = idsForMap.map((id) => toLayer(byId.get(id))).filter((l): l is ActiveLayer => l !== null);

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
  return (
    <div className={mapView
      ? "grid h-screen grid-rows-[auto_1fr] overflow-hidden bg-background text-sm text-foreground"
      : "min-h-screen overflow-x-hidden bg-background text-sm text-foreground"}>
      <header className={`flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-border bg-background px-3 py-2 sm:px-4 ${mapView ? "" : "sticky top-0 z-20"}`}>
        {/* Real <a> (not a button) so cmd/ctrl/middle-click opens the catalog in a new tab; a
            plain click still does in-app SPA nav. href is the viewer root (no search params). */}
        <a href={ROOT_HREF} title="Home — catalog root"
          onClick={(e) => {
            if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return;
            e.preventDefault();
            go({ view: "catalog" });
          }}
          className="flex items-center gap-2 whitespace-nowrap hover:opacity-80">
          <img src={`${import.meta.env.BASE_URL}favicon.svg`} alt="" className="h-5 w-5 shrink-0" />
          <strong className="text-[15px]">UGS Warehouse</strong>
        </a>
        <span className="hidden flex-1 truncate text-xs text-muted-foreground md:block">
          STAC catalog ·{" "}
          <a href={CATALOG_URL} target="_blank" rel="noreferrer"
            className="underline decoration-dotted underline-offset-2 hover:text-foreground">
            {CATALOG_URL.replace(/^https?:\/\//, "")}
          </a>
        </span>
        <span className="hidden whitespace-nowrap text-[11px] text-muted-foreground lg:block"
          title={`viewer build — last updated ${__BUILD_DATE__} (${__BUILD_HASH__})`}>
          updated {__BUILD_DATE__} · {__BUILD_HASH__}
        </span>
        <div className="ml-auto flex gap-1 md:ml-0">
          <span className={tab(view === "catalog")} onClick={() => setView("catalog")}>Catalog</span>
          <span className={tab(view === "map")} onClick={() => setView("map")}>Map</span>
          <span className={tab(view === "search")} onClick={() => setView("search")}>Search</span>
          <span className={tab(view === "arch")} onClick={() => setView("arch")}>Architecture</span>
          <span className={tab(view === "guide")} onClick={() => setView("guide")}>Guide</span>
          {IS_REVIEW && <span className={tab(view === "review")} onClick={() => setView("review")}>Review</span>}
          {IS_REVIEW && <NotifBell onClick={() => setView("review")} />}
          <ThemeToggle />
        </div>
      </header>

      {catalog.error && <p className="p-4 text-destructive">{String(catalog.error)}</p>}

      {view === "review" ? (
        // Review data are vector serving-topics → the ugs-serving-topics collection. Open the item there.
        <ReviewDashboard onOpen={(itemId) => go({ view: "catalog", c: "ugs-serving-topics", i: itemId })} />
      ) : view === "guide" ? (
        <Guide />
      ) : view === "arch" ? (
        <Architecture />
      ) : view === "search" ? (
        <ArticleSearch catalog={catalogDocs} onOpen={(collId, itemId) => go({ view: "catalog", c: collId, i: itemId })} />
      ) : !mapView ? (
        <Browse
          cards={cardsWithCovers}
          collectionId={collectionId}
          allItems={allItems}
          itemsLoading={itemsLoading}
          showItems={Boolean(leafColl)}
          atRoot={!collectionId}
          breadcrumb={crumbs}
          search={search}
          onSearch={setSearch}
          threeD={threeD}
          onThreeD={setThreeD}
          series={seriesSel ?? []}
          onSeries={setSeries}
          item={item.data}
          itemSelected={Boolean(itemUrl)}
          onOpenCollection={openCollection}
          onOpenItem={openItem}
          onOpenCover={openCover}
          onBackToItems={() => go({ view, c: collectionUrl, s: seriesSel })}
          onViewMap={() => go({ view: "map", c: collectionUrl, i: itemUrl, l: itemUrl ? [idOf(itemUrl)] : layerIds })}
        />
      ) : (
        <div className="grid h-full min-h-0 grid-rows-[55vh_1fr] overflow-hidden md:grid-cols-[320px_1fr] md:grid-rows-1">
          <aside className="overflow-auto border-b border-border p-3 md:border-b-0 md:border-r">
            {catalog.isLoading && <p className="text-muted-foreground">Loading catalog…</p>}
            {!leafColl &&
              (subCat ? cards : leafColls).map((c) => (
                <div key={c.href} className={row} onClick={() => openCollection(c.href)}>
                  {c.title ?? c.id}{c.count != null && <span className="text-muted-foreground"> · {c.count}</span>}
                  {c.mappable === 0 && <span className="ml-1 text-[11px] text-muted-foreground">· no map data</span>}
                </div>
              ))}
            {leafColl && (
              <>
                <div className="mb-2 flex items-center justify-between text-xs">
                  <span className="cursor-pointer text-primary" onClick={() => go({ view })}>‹ collections</span>
                  <span className="text-muted-foreground">check to overlay</span>
                </div>
                {allItems.map((it) => {
                  const id = idOf(it.href);
                  const on = idsForMap.includes(id);
                  const ci = activeLayers.findIndex((l) => l.id === id);
                  // Non-blocking hint only — never disable (an item can be "on" via ?i= and must stay
                  // uncheckable). The index carries assets + web-map links, so this is reliable.
                  const noMap = it.data && !cogAsset(it.data) && !pmtilesLink(it.data) && !rasterTilesAsset(it.data);
                  return (
                    <div key={it.href} className={`${row} flex items-center gap-2`}>
                      <input type="checkbox" checked={on} onChange={() => toggleLayer(id)} onClick={(e) => e.stopPropagation()} />
                      {on && ci >= 0 && <span className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: colorFor(ci) }} />}
                      <span className="flex-1 cursor-pointer" onClick={() => openItem(it.href)}>
                        {String(it.data?.properties?.title ?? id)}
                        {noMap && <span className="ml-1 text-[10px] text-muted-foreground">· no map data</span>}
                      </span>
                    </div>
                  );
                })}
              </>
            )}
          </aside>
          <main className="grid h-full min-h-0 grid-rows-[1fr_200px] overflow-hidden md:grid-rows-[1fr_240px]">
            <div className="min-h-0"><ItemMap item={item.data} layers={activeLayers} footprints={footprints} onPickFootprint={openItem} /></div>
            <section className="overflow-auto border-t border-border p-3"><MapDetail item={item.data} loading={item.isLoading} /></section>
          </main>
        </div>
      )}
    </div>
  );
}
