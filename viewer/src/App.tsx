import { loadHeader, setUtahHeaderSettings } from "@utahdts/utah-design-system-header";
import { useEffect, useState } from "react";
import { Architecture } from "./Architecture";
import { Guide } from "./Guide";
import utahLogo from "./assets/utah-logo.png";
import { Browse, type CollectionSummary, type ItemRef } from "./Browse";
import { type ActiveLayer, colorFor, ItemMap } from "./Map";
import { CATALOG_URL, childLinks, cogAsset, itemLinks, pmtilesLink, type StacDoc, useDocs, useIndexes, useStac, defaultStyleUrl } from "./stac";
import { useTheme } from "./theme";

const collIdOf = (url?: string) => url?.split("/").slice(-2)[0];
const idOf = (href: string) => href.split("/").slice(-2)[0]; // item id = its folder name

// `s` = selected data-series codes (DS, OFR, GQ…) — shareable series filter for a collection.
type View = "catalog" | "map" | "arch" | "guide";
type Nav = { view: View; c?: string; i?: string; l?: string[]; s?: string[] };

const readUrl = (): Nav => {
  const p = new URLSearchParams(location.search);
  const l = p.get("l");
  const s = p.get("s");
  const v = p.get("view");
  return {
    view: v === "map" ? "map" : v === "arch" ? "arch" : v === "guide" ? "guide" : "catalog",
    c: p.get("c") || undefined, i: p.get("i") || undefined,
    l: l ? l.split(",").filter(Boolean) : undefined,
    s: s ? s.split(",").filter(Boolean) : undefined,
  };
};

// Write nav state into the URL (preserving ?catalog= and ?m=). push for user navigation
// so back/forward work; replace for the initial sync.
const writeUrl = (n: Nav, push: boolean) => {
  const p = new URLSearchParams(location.search);
  if (n.view === "map") p.set("view", "map");
  else if (n.view === "arch") p.set("view", "arch");
  else if (n.view === "guide") p.set("view", "guide");
  else p.delete("view");
  n.c ? p.set("c", n.c) : p.delete("c");
  n.i ? p.set("i", n.i) : p.delete("i");
  n.l?.length ? p.set("l", n.l.join(",")) : p.delete("l");
  n.s?.length ? p.set("s", n.s.join(",")) : p.delete("s");
  const url = `${location.pathname}${p.toString() ? "?" + p : ""}`;
  (push ? history.pushState : history.replaceState).call(history, null, "", url);
};

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
  return (
    <>
      <h2 className="mb-1.5 text-base font-semibold">{String(p.title ?? item.id ?? "")}</h2>
      <div>
        {Object.entries(item.assets ?? {}).map(([k, a]) => (
          <a key={k} className={asset} href={a.href} target="_blank" rel="noopener">{a.title ?? k}</a>
        ))}
      </div>
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
  const [{ view, c: collectionUrl, i: itemUrl, l: layerIds, s: seriesSel }, setNav] = useState<Nav>(readUrl);

  // Sync state ↔ URL: push on user nav (back/forward works); read URL on popstate.
  const go = (next: Nav, push = true) => { writeUrl(next, push); setNav(next); };
  const setView = (v: View) => go({ view: v, c: collectionUrl, i: itemUrl, l: layerIds, s: seriesSel });
  useEffect(() => {
    const onPop = () => setNav(readUrl());
    addEventListener("popstate", onPop);
    return () => removeEventListener("popstate", onPop);
  }, []);

  // Official State of Utah header — injects the state identity bar + maintained logo above the
  // app (Utah Design System standard). Configured once on mount.
  useEffect(() => {
    setUtahHeaderSettings({
      title: "Utah Geological Survey",
      showTitle: true,
      titleUrl: "https://geology.utah.gov",
      logo: { imageUrl: utahLogo },   // generic State of Utah emblem (until UGS has its own brand)
      mainMenu: false,
    });
    loadHeader();
  }, []);

  const [search, setSearch] = useState("");

  const catalog = useStac(CATALOG_URL);

  // ---- catalog tree (one level of nesting: root → sub-catalog → series collections) ----
  // Counts + titles ride on the child links (warehouse emits ugs:item_count), so the landing
  // and the series chooser render from a single fetch each — no per-collection fan-out. A
  // child whose href ends in catalog.json is a nesting sub-catalog (ugs-publications); the
  // rest are leaf collections. (A pre-nesting flat catalog has only leaf collections — this
  // still works: ugs-publications is then just a leaf you open into items.)
  const rootChildren: CollectionSummary[] = childLinks(catalog.data, CATALOG_URL).map((l) => ({
    id: collIdOf(l.href) ?? l.href, href: l.href, title: l.title, count: l["ugs:item_count"],
    kind: l.href.endsWith("/catalog.json") ? "catalog" : "collection",
  }));
  const subCats = rootChildren.filter((c) => c.kind === "catalog");
  const subDocs = useDocs(subCats.map((c) => c.href));
  const seriesChildren: CollectionSummary[] = subCats.flatMap((sc, i) =>
    childLinks(subDocs.docs[i]?.data, sc.href).map((l) => ({
      id: collIdOf(l.href) ?? l.href, href: l.href, title: l.title,
      count: l["ugs:item_count"], kind: "collection", parentId: sc.id,
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
  const searching = !collectionId && search.trim().length > 0;
  const wantColls = leafColl ? [leafColl] : searching ? leafColls : [];
  const idx = useIndexes(wantColls.map((c) => ({ id: c.id, href: c.href })));

  const itemHrefIn = (collHref: string, id: string) =>
    collHref.replace(/collection\.json(\?.*)?$/, `${encodeURIComponent(id)}/${encodeURIComponent(id)}.json`);

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
  const openItem = (href: string) => go({ view, c: collectionUrl, i: idOf(href), l: layerIds, s: seriesSel });
  const setSeries = (codes: string[]) => go({ view, c: collectionUrl, i: itemUrl, l: layerIds, s: codes });
  const toggleLayer = (id: string) => {
    const set = new Set(layerIds ?? []);
    set.has(id) ? set.delete(id) : set.add(id);
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

  // Map view = locked viewport (the map fills the screen, panels scroll internally).
  // Catalog/detail = a document → the page scrolls naturally, header sticks. (No more
  // scroll-box stuck in the middle of an item page.)
  const mapView = view === "map";
  return (
    <div className={mapView
      ? "grid h-screen grid-rows-[auto_1fr] overflow-hidden bg-background text-sm text-foreground"
      : "min-h-screen bg-background text-sm text-foreground"}>
      <header className={`flex items-center gap-3 border-b border-border bg-background px-3 py-2 sm:px-4 ${mapView ? "" : "sticky top-0 z-20"}`}>
        <button onClick={() => go({ view: "catalog" })} title="Home — catalog root"
          className="flex items-center gap-2 whitespace-nowrap hover:opacity-80">
          <img src={`${import.meta.env.BASE_URL}favicon.svg`} alt="" className="h-5 w-5 shrink-0" />
          <strong className="text-[15px]">UGS Warehouse</strong>
        </button>
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
          <span className={tab(view === "arch")} onClick={() => setView("arch")}>Architecture</span>
          <span className={tab(view === "guide")} onClick={() => setView("guide")}>Guide</span>
          <ThemeToggle />
        </div>
      </header>

      {catalog.error && <p className="p-4 text-destructive">{String(catalog.error)}</p>}

      {view === "guide" ? (
        <Guide />
      ) : view === "arch" ? (
        <Architecture />
      ) : !mapView ? (
        <Browse
          cards={cards}
          collectionId={collectionId}
          allItems={allItems}
          itemsLoading={itemsLoading}
          showItems={Boolean(leafColl)}
          atRoot={!collectionId}
          breadcrumb={crumbs}
          search={search}
          onSearch={setSearch}
          series={seriesSel ?? []}
          onSeries={setSeries}
          item={item.data}
          itemSelected={Boolean(itemUrl)}
          onOpenCollection={openCollection}
          onOpenItem={openItem}
          onBackToItems={() => go({ view, c: collectionUrl, s: seriesSel })}
          onViewMap={() => go({ view: "map", c: collectionUrl, i: itemUrl, l: itemUrl ? [idOf(itemUrl)] : layerIds })}
        />
      ) : (
        <div className="grid h-full min-h-0 grid-rows-[40vh_1fr] overflow-hidden md:grid-cols-[320px_1fr] md:grid-rows-1">
          <aside className="overflow-auto border-b border-border p-3 md:border-b-0 md:border-r">
            {catalog.isLoading && <p className="text-muted-foreground">Loading catalog…</p>}
            {!leafColl &&
              (subCat ? cards : leafColls).map((c) => (
                <div key={c.href} className={row} onClick={() => openCollection(c.href)}>
                  {c.title ?? c.id}{c.count != null && <span className="text-muted-foreground"> · {c.count}</span>}
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
                  return (
                    <div key={it.href} className={`${row} flex items-center gap-2`}>
                      <input type="checkbox" checked={on} onChange={() => toggleLayer(id)} onClick={(e) => e.stopPropagation()} />
                      {on && ci >= 0 && <span className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: colorFor(ci) }} />}
                      <span className="flex-1 cursor-pointer" onClick={() => openItem(it.href)}>{String(it.data?.properties?.title ?? id)}</span>
                    </div>
                  );
                })}
              </>
            )}
          </aside>
          <main className="grid h-full min-h-0 grid-rows-[1fr_200px] overflow-hidden md:grid-rows-[1fr_240px]">
            <div className="min-h-0"><ItemMap item={item.data} layers={activeLayers} /></div>
            <section className="overflow-auto border-t border-border p-3"><MapDetail item={item.data} loading={item.isLoading} /></section>
          </main>
        </div>
      )}
    </div>
  );
}
