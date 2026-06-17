import { loadHeader, setUtahHeaderSettings } from "@utahdts/utah-design-system-header";
import { useEffect, useState } from "react";
import { Browse, type CollectionSummary, type ItemRef } from "./Browse";
import { type ActiveLayer, colorFor, ItemMap } from "./Map";
import { CATALOG_URL, childLinks, itemLinks, pmtilesLink, type StacDoc, useDocs, useStac } from "./stac";
import { useTheme } from "./theme";

const collIdOf = (url?: string) => url?.split("/").slice(-2)[0];
const idOf = (href: string) => href.split("/").slice(-2)[0]; // item id = its folder name

type Nav = { view: "catalog" | "map"; c?: string; i?: string; l?: string[] };

const readUrl = (): Nav => {
  const p = new URLSearchParams(location.search);
  const l = p.get("l");
  return {
    view: p.get("view") === "map" ? "map" : "catalog",
    c: p.get("c") || undefined, i: p.get("i") || undefined,
    l: l ? l.split(",").filter(Boolean) : undefined,
  };
};

// Write nav state into the URL (preserving ?catalog= and ?m=). push for user navigation
// so back/forward work; replace for the initial sync.
const writeUrl = (n: Nav, push: boolean) => {
  const p = new URLSearchParams(location.search);
  n.view === "map" ? p.set("view", "map") : p.delete("view");
  n.c ? p.set("c", n.c) : p.delete("c");
  n.i ? p.set("i", n.i) : p.delete("i");
  n.l?.length ? p.set("l", n.l.join(",")) : p.delete("l");
  const url = `${location.pathname}${p.toString() ? "?" + p : ""}`;
  (push ? history.pushState : history.replaceState).call(history, null, "", url);
};

// An ItemRef → map ActiveLayer (null if it has no PMTiles to render).
function toLayer(ref: ItemRef | undefined): ActiveLayer | null {
  if (!ref?.data) return null;
  const pm = pmtilesLink(ref.data);
  if (!pm) return null;
  const id = idOf(ref.href);
  return {
    id,
    title: String(ref.data.properties?.title ?? id),
    pmHref: pm.href,
    pmLayer: pm["pmtiles:layers"]?.[0] ?? id,
    bbox: ref.data.bbox,
  };
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
  const [{ view, c: collectionUrl, i: itemUrl, l: layerIds }, setNav] = useState<Nav>(readUrl);

  // Sync state ↔ URL: push on user nav (back/forward works); read URL on popstate.
  const go = (next: Nav, push = true) => { writeUrl(next, push); setNav(next); };
  const setView = (v: "catalog" | "map") => go({ view: v, c: collectionUrl, i: itemUrl, l: layerIds });
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
      mainMenu: false,
    });
    loadHeader();
  }, []);

  const catalog = useStac(CATALOG_URL);
  const collections = childLinks(catalog.data, CATALOG_URL);

  // Fetch every collection.json (for item counts + their item links) then every item
  // (for the metadata tables + global search). Fine for hundreds of items; if the
  // catalog grows to thousands this should move behind a stac-geoparquet index.
  const collDocs = useDocs(collections.map((c) => c.href));
  const perColl: CollectionSummary[] = collections.map((c, i) => {
    const doc = collDocs.docs[i]?.data;
    const links = itemLinks(doc, c.href);
    return { id: collIdOf(c.href) ?? c.href, href: c.href, title: c.title,
             description: typeof doc?.description === "string" ? doc.description : undefined,
             count: links.length, itemLinks: links };
  });

  const refs = perColl.flatMap((pc) => pc.itemLinks.map((l) => ({ collId: pc.id, href: l.href })));
  const allDocs = useDocs(refs.map((r) => r.href));
  const allItems: ItemRef[] = refs.map((r, i) => ({ ...r, data: allDocs.docs[i]?.data }));

  // c/i in the URL may be a short id (clean + shareable: ?c=ugs-publications&i=GQ-1560) or a
  // full STAC URL (older links). collIdOf/idOf already no-op on a bare id; resolve i to an
  // absolute href for fetching, accepting both forms.
  const isUrl = (s?: string) => Boolean(s) && /^https?:\/\//.test(s as string);
  const collectionId = collIdOf(collectionUrl);
  const selColl = perColl.find((pc) => pc.id === collectionId);
  const itemHref = isUrl(itemUrl)
    ? itemUrl
    : selColl?.itemLinks.find((l) => idOf(l.href) === itemUrl)?.href
      ?? allItems.find((r) => idOf(r.href) === itemUrl)?.href;
  const item = useStac(itemHref);

  const openCollection = (href: string) => go({ view, c: collIdOf(href) });
  const openItem = (href: string) => go({ view, c: collectionUrl, i: idOf(href), l: layerIds });
  const toggleLayer = (id: string) => {
    const set = new Set(layerIds ?? []);
    set.has(id) ? set.delete(id) : set.add(id);
    go({ view, c: collectionUrl, i: itemUrl, l: [...set] });
  };

  // Active map layers: the toggled set, else fall back to the detail item (so a plain
  // ?c=&i= link still shows its layer). Resolved against fetched item data (for PMTiles).
  const byId = new Map(allItems.map((r) => [idOf(r.href), r]));
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
        <strong className="text-[15px] whitespace-nowrap">UGS Warehouse</strong>
        <span className="hidden flex-1 truncate text-xs text-muted-foreground md:block">STAC catalog · cloud-native geospatial</span>
        <div className="ml-auto flex gap-1 md:ml-0">
          <span className={tab(view === "catalog")} onClick={() => setView("catalog")}>Catalog</span>
          <span className={tab(view === "map")} onClick={() => setView("map")}>Map</span>
          <ThemeToggle />
        </div>
      </header>

      {catalog.error && <p className="p-4 text-destructive">{String(catalog.error)}</p>}

      {!mapView ? (
        <Browse
          collections={perColl}
          collectionId={collectionId}
          allItems={allItems}
          itemsLoading={allDocs.isLoading}
          item={item.data}
          itemSelected={Boolean(itemUrl)}
          onOpenCollection={openCollection}
          onOpenItem={openItem}
          onBackToCollections={() => go({ view })}
          onBackToItems={() => go({ view, c: collectionUrl })}
          onViewMap={() => go({ view: "map", c: collectionUrl, i: itemUrl, l: itemUrl ? [idOf(itemUrl)] : layerIds })}
        />
      ) : (
        <div className="grid h-full min-h-0 grid-rows-[40vh_1fr] overflow-hidden md:grid-cols-[320px_1fr] md:grid-rows-1">
          <aside className="overflow-auto border-b border-border p-3 md:border-b-0 md:border-r">
            {catalog.isLoading && <p className="text-muted-foreground">Loading catalog…</p>}
            {!collectionUrl &&
              perColl.map((c) => (
                <div key={c.href} className={row} onClick={() => openCollection(c.href)}>
                  {c.title ?? c.id} <span className="text-muted-foreground">· {c.count}</span>
                </div>
              ))}
            {collectionUrl && (
              <>
                <div className="mb-2 flex items-center justify-between text-xs">
                  <span className="cursor-pointer text-primary" onClick={() => go({ view })}>‹ collections</span>
                  <span className="text-muted-foreground">check to overlay</span>
                </div>
                {(selColl?.itemLinks ?? []).map((it) => {
                  const id = idOf(it.href);
                  const on = idsForMap.includes(id);
                  const ci = activeLayers.findIndex((l) => l.id === id);
                  return (
                    <div key={it.href} className={`${row} flex items-center gap-2`}>
                      <input type="checkbox" checked={on} onChange={() => toggleLayer(id)} onClick={(e) => e.stopPropagation()} />
                      {on && ci >= 0 && <span className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: colorFor(ci) }} />}
                      <span className="flex-1 cursor-pointer" onClick={() => openItem(it.href)}>{it.title ?? id}</span>
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
