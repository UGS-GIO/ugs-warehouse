// Catalog-centric browser: metadata over map. Collection cards (with counts) +
// search-all → sortable item table / cards → item detail. The map is one link out.
import { useMemo, useState } from "react";
import { exportItem, type ExportFormat, FORMATS } from "./download";
import { type Asset, citeLink, type Link, type StacDoc, thumbnailAsset, viaLink } from "./stac";

export type CollectionSummary = {
  id: string; href: string; title?: string; description?: string; count: number; itemLinks: Link[];
};
export type ItemRef = { collId: string; href: string; data?: StacDoc };

const C = {
  wrap: "h-full w-full overflow-auto px-3 py-4 mx-auto max-w-[1180px] sm:px-5",
  crumb: "text-primary cursor-pointer",
  muted: "text-xs text-muted-foreground",
  grid: "mt-3.5 grid gap-3 grid-cols-1 sm:grid-cols-2 lg:grid-cols-3",
  card: "rounded-lg border border-border bg-card px-3.5 py-3 cursor-pointer hover:border-primary hover:shadow-sm transition",
  cardTitle: "mb-1.5 text-sm font-semibold leading-tight",
  badge: "mr-1.5 mt-1 inline-block rounded border border-border bg-muted px-1.5 py-px text-[11px] text-muted-foreground",
  chip: "mr-1.5 mt-1.5 inline-block rounded bg-primary px-2 py-0.5 text-[11px] text-primary-foreground no-underline hover:opacity-90",
  input: "w-72 rounded-md border border-input bg-card px-2.5 py-1.5 text-sm text-foreground placeholder:text-muted-foreground",
  bar: "my-2 flex flex-wrap items-center gap-2.5",
  th: "cursor-pointer whitespace-nowrap border-b border-border px-2.5 py-1.5 text-left text-[11px] uppercase tracking-wide text-muted-foreground",
  thPlain: "whitespace-nowrap border-b border-border px-2.5 py-1.5 text-left text-[11px] uppercase tracking-wide text-muted-foreground",
  td: "border-b border-border px-2.5 py-1.5 align-top text-sm",
};
const toggle = (on: boolean) =>
  `cursor-pointer border border-border px-2.5 py-1 text-xs text-foreground ${on ? "bg-accent" : "bg-card"}`;

const BADGE_KEYS = ["ugs:series", "ugs:pub_type", "ugs:topic", "ugs:scale", "ugs:author"];

const idFromHref = (href: string) => href.split("/").slice(-2)[0];
const props = (it: ItemRef) => it.data?.properties ?? {};
// The STAC item id IS the publication series id (DS-8, OFR-647, …) / the layer stem.
const gSeries = (it: ItemRef) => String(it.data?.id ?? idFromHref(it.href));
const gTitle = (it: ItemRef) => String(props(it).title ?? it.data?.id ?? idFromHref(it.href));
const gDate = (it: ItemRef) => (typeof props(it).datetime === "string" ? (props(it).datetime as string).slice(0, 10) : "");
const gType = (it: ItemRef) => String(props(it)["ugs:pub_type"] ?? props(it)["ugs:series"] ?? props(it)["ugs:topic"] ?? "");
const gScale = (it: ItemRef) => String(props(it)["ugs:scale"] ?? "");
const haystack = (it: ItemRef) => (it.href + JSON.stringify(it.data?.properties ?? {})).toLowerCase();

type SortKey = "id" | "title" | "date" | "type";
const sorters: Record<SortKey, (a: ItemRef, b: ItemRef) => number> = {
  id: (a, b) => gSeries(a).localeCompare(gSeries(b), undefined, { numeric: true }),
  title: (a, b) => gTitle(a).localeCompare(gTitle(b)),
  date: (a, b) => gDate(a).localeCompare(gDate(b)),
  type: (a, b) => gType(a).localeCompare(gType(b)),
};

function AssetChips({ assets }: { assets: Record<string, Asset> }) {
  return (
    <>
      {Object.entries(assets).map(([k, a]) => (
        <a key={k} className={C.chip} href={a.href} target="_blank" rel="noopener"
          onClick={(e) => e.stopPropagation()}>{a.title ?? k}</a>
      ))}
    </>
  );
}

const parquetAsset = (item: StacDoc): Asset | undefined =>
  Object.values(item.assets ?? {}).find(
    (a) => a.type?.includes("parquet") || a.href.endsWith(".parquet"),
  );

// Client-side export — only for items with a GeoParquet asset (serving topics).
function ExportPanel({ item }: { item: StacDoc }) {
  const parquet = parquetAsset(item);
  const fullBbox = item.bbox?.slice(0, 4) as [number, number, number, number] | undefined;
  const [busy, setBusy] = useState<ExportFormat | null>(null);
  const [err, setErr] = useState<string>();
  const [clipOn, setClipOn] = useState(false);
  const [bbox, setBbox] = useState<[number, number, number, number]>(fullBbox ?? [0, 0, 0, 0]);
  if (!parquet) return null;

  const run = async (fmt: ExportFormat) => {
    setErr(undefined);
    setBusy(fmt);
    try {
      await exportItem(parquet.href, String(item.id ?? "export"), fmt, clipOn ? bbox : undefined);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const labels = ["W", "S", "E", "N"];
  return (
    <div className="mt-3 rounded-lg border border-border bg-muted p-3">
      <div className="mb-1.5 text-xs font-semibold text-muted-foreground">Download as</div>
      <div className="flex flex-wrap items-center gap-2">
        {FORMATS.map((f) => (
          <button key={f.id} disabled={busy !== null} onClick={() => run(f.id)}
            className="rounded border border-border bg-card px-2.5 py-1 text-xs text-foreground hover:border-primary disabled:opacity-50">
            {busy === f.id ? "preparing…" : f.label}
          </button>
        ))}
        {busy && <span className={C.muted}>running in your browser · first export loads DuckDB (~a few MB)</span>}
      </div>
      {fullBbox && (
        <div className="mt-2 text-xs">
          <label className="flex items-center gap-1.5 text-muted-foreground">
            <input type="checkbox" checked={clipOn} onChange={(e) => setClipOn(e.target.checked)} />
            Clip to area (bbox, EPSG:4326)
          </label>
          {clipOn && (
            <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
              {bbox.map((v, i) => (
                <label key={i} className="flex items-center gap-1 text-muted-foreground">
                  {labels[i]}
                  <input type="number" step="0.01" value={v}
                    onChange={(e) => setBbox((b) => b.map((x, j) => (j === i ? Number(e.target.value) : x)) as typeof b)}
                    className="w-24 rounded border border-input bg-card px-1.5 py-0.5 text-foreground" />
                </label>
              ))}
              <button onClick={() => setBbox(fullBbox)} className="text-primary">reset</button>
            </div>
          )}
        </div>
      )}
      {err && <div className="mt-1.5 text-xs text-destructive">Export failed: {err}</div>}
    </div>
  );
}

// ---- collection cards ----
const humanize = (id: string) =>
  id.replace(/^ugs-/, "").replace(/[-_]+/g, " ").replace(/\b\w/g, (ch) => ch.toUpperCase());
// The warehouse emits a placeholder "UGS warehouse — {id}." description; hide it as noise.
const meaningfulDesc = (d?: string) => (d && !/^UGS warehouse — .*\.$/.test(d) ? d : null);

function Collections({ collections, onOpen }: { collections: CollectionSummary[]; onOpen: (href: string) => void }) {
  if (!collections.length) return <p className={`${C.muted} mt-4`}>No collections in this catalog yet.</p>;
  return (
    <>
      <h2 className="mb-1 mt-1 text-lg font-semibold">Collections</h2>
      <div className={C.grid}>
        {collections.map((c) => {
          const desc = meaningfulDesc(c.description);
          return (
            <div key={c.href} className={C.card} onClick={() => onOpen(c.href)}>
              <p className="text-base font-semibold leading-tight">{c.title ?? humanize(c.id)}</p>
              <div className="mt-0.5 font-mono text-[11px] text-muted-foreground">{c.id}</div>
              {desc && <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">{desc}</p>}
              <span className={`${C.badge} mt-2`}>{c.count} item{c.count === 1 ? "" : "s"}</span>
            </div>
          );
        })}
      </div>
    </>
  );
}

// ---- item list: filter + sort + table/cards, reused for a collection and global search ----
function ItemList({ items, showCollection, query, onOpen }: {
  items: ItemRef[]; showCollection?: boolean; query?: string; onOpen: (href: string) => void;
}) {
  const [q, setQ] = useState("");
  const [mode, setMode] = useState<"table" | "cards">("table");
  const [sort, setSort] = useState<SortKey>("title");
  const [asc, setAsc] = useState(true);

  const needle = (query ?? q).trim().toLowerCase();
  const rows = useMemo(() => {
    const f = needle ? items.filter((it) => haystack(it).includes(needle)) : items;
    const s = [...f].sort(sorters[sort]);
    return asc ? s : s.reverse();
  }, [items, needle, sort, asc]);

  const head = (key: SortKey, label: string) => (
    <th className={C.th} onClick={() => (sort === key ? setAsc(!asc) : (setSort(key), setAsc(true)))}>
      {label}{sort === key ? (asc ? " ▲" : " ▼") : ""}
    </th>
  );

  return (
    <>
      <div className={C.bar}>
        {query === undefined && (
          <input className={C.input} placeholder="Filter…" value={q} onChange={(e) => setQ(e.target.value)} />
        )}
        <span className={C.muted}>{rows.length} of {items.length}</span>
        <span className="flex-1" />
        <span className={toggle(mode === "table")} onClick={() => setMode("table")}>Table</span>
        <span className={toggle(mode === "cards")} onClick={() => setMode("cards")}>Cards</span>
      </div>

      {rows.length === 0 ? (
        <p className={`${C.muted} mt-3`}>{needle ? "No items match." : "No items."}</p>
      ) : mode === "table" ? (
        <div className="overflow-x-auto">
        <table className="w-full border-collapse">
          <thead>
            <tr>
              {head("id", "ID")}
              {head("title", "Title")}
              {showCollection && <th className={C.thPlain}>Collection</th>}
              {head("type", "Type")}
              {head("date", "Date")}
              <th className={C.thPlain}>Scale</th>
              <th className={C.thPlain}>Assets</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((it) => (
              <tr key={it.href} className="cursor-pointer hover:bg-muted" onClick={() => onOpen(it.href)}>
                <td className={`${C.td} whitespace-nowrap font-mono text-[13px] font-semibold text-foreground`}>{gSeries(it)}</td>
                <td className={`${C.td} text-primary`}>{gTitle(it)}</td>
                {showCollection && <td className={C.td}>{it.collId}</td>}
                <td className={C.td}>{gType(it)}</td>
                <td className={C.td}>{gDate(it)}</td>
                <td className={C.td}>{gScale(it)}</td>
                <td className={C.td}>{it.data?.assets ? <AssetChips assets={it.data.assets} /> : ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      ) : (
        <div className={C.grid}>
          {rows.map((it) => (
            <div key={it.href} className={C.card} onClick={() => onOpen(it.href)}>
              <div className="font-mono text-[12px] font-semibold text-foreground">{gSeries(it)}</div>
              <p className={C.cardTitle}>{gTitle(it)}</p>
              <div>
                {showCollection && <span className={C.badge}>{it.collId}</span>}
                {gDate(it) && <span className={C.badge}>{gDate(it)}</span>}
                {BADGE_KEYS.filter((k) => props(it)[k]).map((k) => (
                  <span key={k} className={C.badge}>{String(props(it)[k])}</span>
                ))}
              </div>
              {it.data?.assets && <div><AssetChips assets={it.data.assets} /></div>}
            </div>
          ))}
        </div>
      )}
    </>
  );
}

// Coordinate rings from a Polygon/MultiPolygon, or synthesized from a bbox.
function previewRings(geom: GeoJSON.Geometry | null | undefined, bbox?: number[]): number[][][] {
  if (geom?.type === "Polygon") return geom.coordinates as number[][][];
  if (geom?.type === "MultiPolygon") return (geom.coordinates as number[][][][]).flat();
  if (bbox && bbox.length >= 4) {
    const [w, s, e, n] = bbox;
    return [[[w, s], [e, s], [e, n], [w, n], [w, s]]];
  }
  return [];
}

// Preview: the harvested thumbnail image where present, else a zero-dependency SVG of the
// footprint (no map tiles, no network) so every spatial item shows a "where + shape" glance.
function Preview({ item }: { item: StacDoc }) {
  const thumb = thumbnailAsset(item);
  if (thumb) {
    return (
      <img src={thumb.href} alt={thumb.title ?? "preview"} loading="lazy"
        className="mt-2 max-h-60 w-auto rounded-md border border-border bg-muted object-contain" />
    );
  }
  const rings = previewRings(item.geometry, item.bbox);
  if (!rings.length) return null;
  const pts = rings.flat();
  const xs = pts.map((p) => p[0]);
  const ys = pts.map((p) => p[1]);
  const minx = Math.min(...xs), maxx = Math.max(...xs), miny = Math.min(...ys), maxy = Math.max(...ys);
  const w = maxx - minx || 1, h = maxy - miny || 1, pad = 0.1, W = 260, H = 180;
  const sx = (x: number) => ((x - minx) / w) * (1 - 2 * pad) * W + pad * W;
  const sy = (y: number) => H - (((y - miny) / h) * (1 - 2 * pad) * H + pad * H); // flip: lat up
  const d = rings
    .map((r) => "M" + r.map((p) => `${sx(p[0]).toFixed(1)},${sy(p[1]).toFixed(1)}`).join("L") + "Z")
    .join(" ");
  return (
    <svg width={W} height={H} className="mt-2 rounded-md border border-border bg-muted"
      role="img" aria-label="footprint preview">
      <path d={d} fill="rgba(209,73,28,0.22)" stroke="#d1491c" strokeWidth={1.3} strokeLinejoin="round" />
    </svg>
  );
}

// ---- item detail ----
function ItemDetail({ collectionId, item, onBack, onMap }: {
  collectionId: string; item?: StacDoc; onBack: () => void; onMap: () => void;
}) {
  if (!item) return <em className={C.muted}>Loading…</em>;
  const p = item.properties ?? {};
  const hasGeom = Boolean(item.geometry || item.bbox);
  const via = viaLink(item);
  const cite = citeLink(item);
  return (
    <>
      <div className="mb-2.5">
        <span className={C.crumb} onClick={onBack}>{collectionId}</span>
        <span className={C.muted}> / {item.id}</span>
      </div>
      <div className="font-mono text-sm font-semibold text-primary">{item.id}</div>
      <h2 className="mb-1 text-xl font-semibold">{String(p.title ?? item.id ?? "")}</h2>
      {typeof p.description === "string" && <p className="max-w-[760px] text-muted-foreground">{p.description}</p>}
      <Preview item={item} />
      {item.assets && <div className="my-2"><AssetChips assets={item.assets} /></div>}
      <div className="mt-1.5 flex flex-wrap gap-2">
        {hasGeom && (
          <button onClick={onMap}
            className="inline-block rounded bg-emerald-700 px-2.5 py-1 text-[11px] text-white hover:bg-emerald-800">
            View on map ›
          </button>
        )}
        {via && (
          <a href={via.href} target="_blank" rel="noopener"
            className="inline-block rounded bg-primary px-2.5 py-1 text-[11px] text-primary-foreground no-underline hover:opacity-90">
            {via.title ?? "Publication page"} ↗
          </a>
        )}
        {cite && (
          <a href={cite.href} target="_blank" rel="noopener"
            className="inline-block rounded border border-border px-2.5 py-1 text-[11px] text-foreground no-underline hover:border-primary">
            Cite (DOI) ↗
          </a>
        )}
      </div>
      <ExportPanel item={item} />
      <table className="mt-3 w-full max-w-[760px] border-collapse text-sm">
        <tbody>
          {Object.entries(p).filter(([, v]) => v !== null && v !== "").map(([k, v]) => (
            <tr key={k}>
              <td className="whitespace-nowrap border-b border-border px-2.5 py-1 align-top text-muted-foreground">{k}</td>
              <td className="border-b border-border px-2.5 py-1">{String(v)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

export function Browse(props: {
  collections: CollectionSummary[];
  collectionId?: string;
  allItems: ItemRef[];
  itemsLoading: boolean;
  item?: StacDoc;
  itemSelected: boolean;
  onOpenCollection: (href: string) => void;
  onOpenItem: (href: string) => void;
  onBackToCollections: () => void;
  onBackToItems: () => void;
  onViewMap: () => void;
}) {
  const [search, setSearch] = useState("");
  const { collectionId, itemSelected } = props;

  // item detail
  if (collectionId && itemSelected) {
    return (
      <div className={C.wrap}>
        <ItemDetail collectionId={collectionId} item={props.item}
          onBack={props.onBackToItems} onMap={props.onViewMap} />
      </div>
    );
  }

  // a collection's items
  if (collectionId) {
    const items = props.allItems.filter((it) => it.collId === collectionId);
    return (
      <div className={C.wrap}>
        <div className="mb-1">
          <span className={C.crumb} onClick={props.onBackToCollections}>Collections</span>
          <span className={C.muted}> / {collectionId}</span>
          {props.itemsLoading && <span className={C.muted}> · loading…</span>}
        </div>
        <ItemList items={items} onOpen={props.onOpenItem} />
      </div>
    );
  }

  // catalog root: search-all OR collection cards
  return (
    <div className={C.wrap}>
      <div className={C.bar}>
        <input className={C.input} placeholder="Search all collections…" value={search}
          onChange={(e) => setSearch(e.target.value)} />
        {props.itemsLoading && <span className={C.muted}>indexing items…</span>}
      </div>
      {search.trim()
        ? <ItemList items={props.allItems} showCollection query={search} onOpen={props.onOpenItem} />
        : <Collections collections={props.collections} onOpen={props.onOpenCollection} />}
    </div>
  );
}
