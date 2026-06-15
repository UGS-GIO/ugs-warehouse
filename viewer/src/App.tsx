import { useEffect, useState } from "react";
import { Browse, type CollectionSummary, type ItemRef } from "./Browse";
import { ItemMap } from "./Map";
import { CATALOG_URL, childLinks, itemLinks, type StacDoc, useDocs, useStac } from "./stac";

const collIdOf = (url?: string) => url?.split("/").slice(-2)[0];

type Nav = { view: "catalog" | "map"; c?: string; i?: string };

const readUrl = (): Nav => {
  const p = new URLSearchParams(location.search);
  return { view: p.get("view") === "map" ? "map" : "catalog", c: p.get("c") || undefined, i: p.get("i") || undefined };
};

// Write nav state into the URL (preserving ?catalog=). push for user navigation so
// back/forward work; replace for the initial sync.
const writeUrl = (n: Nav, push: boolean) => {
  const p = new URLSearchParams(location.search);
  n.view === "map" ? p.set("view", "map") : p.delete("view");
  n.c ? p.set("c", n.c) : p.delete("c");
  n.i ? p.set("i", n.i) : p.delete("i");
  const url = `${location.pathname}${p.toString() ? "?" + p : ""}`;
  (push ? history.pushState : history.replaceState).call(history, null, "", url);
};

const tab = (on: boolean) =>
  `cursor-pointer rounded-md border border-gray-300 px-3 py-1.5 text-[13px] ${on ? "bg-blue-600 text-white" : "bg-white text-gray-700"}`;
const asset = "mr-1.5 mt-0.5 inline-block rounded bg-blue-600 px-2 py-1 text-xs text-white no-underline hover:bg-blue-700";
const row = "mb-1.5 cursor-pointer rounded-md border border-gray-200 bg-gray-50 px-2 py-1.5 break-all hover:border-blue-400";

function MapDetail({ item, loading }: { item?: StacDoc; loading: boolean }) {
  if (loading) return <em>Loading item…</em>;
  if (!item) return <em className="text-gray-400">Pick an item to see detail, footprint, and assets.</em>;
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
              <td className="whitespace-nowrap px-2 py-0.5 align-top text-gray-500">{k}</td>
              <td className="border-b border-gray-100 px-2 py-0.5">{String(v)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

export function App() {
  const [{ view, c: collectionUrl, i: itemUrl }, setNav] = useState<Nav>(readUrl);

  // Sync state ↔ URL: push on user nav (back/forward works); read URL on popstate.
  const go = (next: Nav, push = true) => { writeUrl(next, push); setNav(next); };
  const setView = (v: "catalog" | "map") => go({ view: v, c: collectionUrl, i: itemUrl });
  useEffect(() => {
    const onPop = () => setNav(readUrl());
    addEventListener("popstate", onPop);
    return () => removeEventListener("popstate", onPop);
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

  const item = useStac(itemUrl);
  const collectionId = collIdOf(collectionUrl);
  const selColl = perColl.find((pc) => pc.id === collectionId);

  const openCollection = (href: string) => go({ view, c: href });
  const openItem = (href: string) => go({ view, c: collectionUrl, i: href });

  return (
    <div className="grid h-screen grid-rows-[auto_1fr] text-sm text-gray-900">
      <header className="flex items-baseline gap-3 border-b border-gray-300 px-4 py-2">
        <strong className="text-[15px]">UGS Warehouse</strong>
        <span className="flex-1 break-all text-[11px] text-gray-500">{CATALOG_URL}</span>
        <div className="flex gap-1">
          <span className={tab(view === "catalog")} onClick={() => setView("catalog")}>Catalog</span>
          <span className={tab(view === "map")} onClick={() => setView("map")}>Map</span>
        </div>
      </header>

      {catalog.error && <p className="p-4 text-red-700">{String(catalog.error)}</p>}

      {view === "catalog" ? (
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
          onViewMap={() => go({ view: "map", c: collectionUrl, i: itemUrl })}
        />
      ) : (
        <div className="grid h-full grid-cols-[320px_1fr] overflow-hidden">
          <aside className="overflow-auto border-r border-gray-300 p-3">
            {catalog.isLoading && <p>Loading catalog…</p>}
            {!collectionUrl &&
              perColl.map((c) => (
                <div key={c.href} className={row} onClick={() => openCollection(c.href)}>
                  {c.title ?? c.id} <span className="text-gray-400">· {c.count}</span>
                </div>
              ))}
            {collectionUrl && (
              <>
                <div className="mb-2 cursor-pointer text-xs text-blue-600"
                  onClick={() => go({ view })}>‹ collections</div>
                {(selColl?.itemLinks ?? []).map((it) => (
                  <div key={it.href} className={row} onClick={() => openItem(it.href)}>{it.title ?? it.href.split("/").slice(-1)[0]}</div>
                ))}
              </>
            )}
          </aside>
          <main className="grid h-full grid-rows-[1fr_240px] overflow-hidden">
            <div><ItemMap item={item.data} /></div>
            <section className="overflow-auto border-t border-gray-300 p-3"><MapDetail item={item.data} loading={item.isLoading} /></section>
          </main>
        </div>
      )}
    </div>
  );
}
