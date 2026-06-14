import { useState } from "react";
import { ItemMap } from "./Map";
import { CATALOG_URL, childLinks, itemLinks, type StacDoc, useStac } from "./stac";

const S = {
  app: { display: "grid", gridTemplateColumns: "340px 1fr", height: "100vh", font: "14px/1.4 system-ui, sans-serif" } as const,
  side: { borderRight: "1px solid #ddd", overflow: "auto", padding: 12 } as const,
  sub: { color: "#666", fontSize: 12, margin: "2px 0 10px", wordBreak: "break-all" } as const,
  row: { padding: "7px 9px", border: "1px solid #e5e5e5", borderRadius: 6, marginBottom: 6, cursor: "pointer", background: "#fafafa" } as const,
  main: { display: "grid", gridTemplateRows: "1fr 280px", height: "100vh" } as const,
  detail: { borderTop: "1px solid #ddd", overflow: "auto", padding: 12 } as const,
  link: { color: "#2b6cdf", cursor: "pointer", fontSize: 12 } as const,
  asset: { display: "inline-block", margin: "3px 6px 0 0", padding: "4px 8px", borderRadius: 5, background: "#2b6cdf", color: "#fff", textDecoration: "none", fontSize: 12 } as const,
};

function Row({ title, onClick }: { title: string; onClick: () => void }) {
  return <div style={S.row} onClick={onClick}>{title}</div>;
}

function Detail({ item, loading }: { item?: StacDoc; loading: boolean }) {
  if (loading) return <em>Loading item…</em>;
  if (!item) return <em style={{ color: "#888" }}>Pick an item to see detail, footprint, and assets.</em>;
  const p = item.properties ?? {};
  return (
    <>
      <h2 style={{ margin: "0 0 6px", fontSize: 16 }}>{String(p.title ?? item.id ?? "")}</h2>
      <div>
        {Object.entries(item.assets ?? {}).map(([k, a]) => (
          <a key={k} style={S.asset} href={a.href} target="_blank" rel="noopener">{a.title ?? k}</a>
        ))}
      </div>
      <table style={{ borderCollapse: "collapse", width: "100%", marginTop: 8, fontSize: 13 }}>
        <tbody>
          {Object.entries(p).filter(([, v]) => v !== null && v !== "").map(([k, v]) => (
            <tr key={k}>
              <td style={{ color: "#666", whiteSpace: "nowrap", padding: "3px 8px", verticalAlign: "top" }}>{k}</td>
              <td style={{ padding: "3px 8px", borderBottom: "1px solid #f0f0f0" }}>{String(v)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

export function App() {
  const [collectionUrl, setCollectionUrl] = useState<string>();
  const [itemUrl, setItemUrl] = useState<string>();

  const catalog = useStac(CATALOG_URL);
  const collection = useStac(collectionUrl);
  const item = useStac(itemUrl);

  const collections = childLinks(catalog.data, CATALOG_URL);
  const items = itemLinks(collection.data, collectionUrl ?? CATALOG_URL);
  const inCollection = Boolean(collectionUrl);

  return (
    <div style={S.app}>
      <aside style={S.side}>
        <h1 style={{ fontSize: 15, margin: "0 0 2px" }}>UGS Warehouse</h1>
        <div style={S.sub}>{CATALOG_URL}</div>

        {catalog.isLoading && <p>Loading catalog…</p>}
        {catalog.error && <p style={{ color: "#b00" }}>{String(catalog.error)}</p>}

        {!inCollection &&
          collections.map((c) => (
            <Row key={c.href} title={c.title ?? c.href}
              onClick={() => { setCollectionUrl(c.href); setItemUrl(undefined); }} />
          ))}

        {inCollection && (
          <>
            <div style={{ ...S.link, marginBottom: 8 }}
              onClick={() => { setCollectionUrl(undefined); setItemUrl(undefined); }}>‹ collections</div>
            {collection.isLoading && <p>Loading…</p>}
            {items.map((it) => (
              <Row key={it.href} title={it.title ?? it.href} onClick={() => setItemUrl(it.href)} />
            ))}
          </>
        )}
      </aside>

      <main style={S.main}>
        <div><ItemMap item={item.data} /></div>
        <section style={S.detail}><Detail item={item.data} loading={item.isLoading} /></section>
      </main>
    </div>
  );
}
