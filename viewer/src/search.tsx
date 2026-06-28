// Unified search — client-side, no server. Two sources, one in-browser MiniSearch index:
//   • Survey Notes article FULL TEXT (the warehouse-built corpus, pubs/search/corpus.json)
//   • EVERY catalog item's metadata (title / id / keywords / topic) — pubs, maps, vector layers
// so a query hits article bodies AND any publication or layer. Results link the PDF page and the
// in-app catalog detail.
import { useQuery } from "@tanstack/react-query";
import MiniSearch from "minisearch";
import { type ReactNode, useMemo, useState } from "react";

import { searchPubs } from "./ftsearch";
import { semanticSearch } from "./vsearch";

// A small toggle chip for the search filters.
function Chip({ on, onClick, children }: { on: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button onClick={onClick} className={`rounded border px-2 py-0.5 ${on
      ? "border-primary bg-primary text-primary-foreground"
      : "border-border bg-card text-foreground hover:bg-accent"}`}>{children}</button>
  );
}

export const CORPUS_URL = new URL(
  new URLSearchParams(location.search).get("searchCorpus")
    || "https://maps-assets.geology.utah.gov/pubs/search/corpus.json",
  location.href,
).href;

export type Article = {
  id: string; sid: string; volume: number | null; issue?: string;
  pdf?: string; title: string; page: number | null; text: string;
};
// A catalog item flattened for search (pub or vector layer). Passed in from App's loaded indexes.
export type CatalogDoc = {
  id: string; collId: string; itemId: string; title: string; keywords?: string; meta?: string;
};
type Hit = { id: string; kind: "article" | "item"; title: string; text?: string;
  sid?: string; pdf?: string; page?: number | null; volume?: number | null; issue?: string;
  collId?: string; itemId?: string; score: number };

const seriesCode = (sid: string) => sid.match(/^[A-Za-z]+/)?.[0]?.toUpperCase() ?? sid;
const esc = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

function useCorpus(enabled: boolean) {
  return useQuery({
    queryKey: ["article-corpus", CORPUS_URL],
    enabled, staleTime: Infinity, retry: false,
    queryFn: async () => {
      const r = await fetch(CORPUS_URL);
      if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
      return (await r.json()) as Article[];
    },
  });
}

function Snippet({ text, q }: { text: string; q: string }) {
  const terms = q.toLowerCase().split(/\s+/).filter((t) => t.length > 1);
  const lower = text.toLowerCase();
  let at = -1;
  for (const t of terms) { const i = lower.indexOf(t); if (i >= 0) { at = i; break; } }
  const start = at < 0 ? 0 : Math.max(0, at - 60);
  const body = (start ? "…" : "") + text.slice(start, start + 220).trim() + "…";
  const re = terms.length ? new RegExp(`(${terms.sort((a, b) => b.length - a.length).map(esc).join("|")})`, "gi") : null;
  const parts = re ? body.split(re) : [body];
  return (
    <p className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">
      {parts.map((p, i) => (re && re.test(p)
        ? <mark key={i} className="bg-yellow-200 text-foreground dark:bg-yellow-500/40">{p}</mark>
        : <span key={i}>{p}</span>))}
    </p>
  );
}

export function ArticleSearch({ catalog = [], onOpen }: {
  catalog?: CatalogDoc[]; onOpen?: (collId: string, itemId: string) => void;
}) {
  const [q, setQ] = useState("");
  const [kind, setKind] = useState<"all" | "article" | "item">("all");
  const [colls, setColls] = useState<string[]>([]);
  const [fullText, setFullText] = useState(false);
  // Heavy full-text-of-every-pub search (duckdb-wasm over the remote FTS db) — only runs when the
  // toggle is on, and only re-queries when the text settles (manual trigger via the query key).
  const pubFts = useQuery({
    queryKey: ["pubfts", q],
    queryFn: () => searchPubs(q),
    enabled: fullText && q.trim().length >= 2,
    staleTime: 60_000, retry: false,
  });
  const [semantic, setSemantic] = useState(false);
  // Semantic (meaning-based) search — embeds the query in-browser + vector search via duckdb-wasm.
  const sem = useQuery({
    queryKey: ["vss", q],
    queryFn: () => semanticSearch(q),
    enabled: semantic && q.trim().length >= 2,
    staleTime: 60_000, retry: false,
  });
  const corpus = useCorpus(true);
  const index = useMemo(() => buildIndex(corpus.data ?? [], catalog), [corpus.data, catalog]);
  const raw = useMemo(() => (q.trim().length < 2 ? [] : index.search(q) as unknown as Hit[]), [index, q]);

  // Facets over the current matches: the publication collections present (for the item filter).
  const collFacets = useMemo(() => {
    const m = new Map<string, number>();
    for (const r of raw) if (r.kind === "item" && r.collId) m.set(r.collId, (m.get(r.collId) ?? 0) + 1);
    return [...m.entries()].sort((a, b) => b[1] - a[1]);
  }, [raw]);
  const nArt = raw.filter((r) => r.kind === "article").length;
  const sel = new Set(colls);
  const toggleColl = (c: string) => setColls(sel.has(c) ? colls.filter((x) => x !== c) : [...colls, c]);

  const results = useMemo(() => raw.filter((r) => {
    if (kind !== "all" && r.kind !== kind) return false;
    if (r.kind === "item" && colls.length && !(r.collId && sel.has(r.collId))) return false;
    return true;
  }).slice(0, 60), [raw, kind, colls]);  // eslint-disable-line react-hooks/exhaustive-deps

  const nArticles = corpus.data?.length ?? 0;
  return (
    <div className="mx-auto max-w-3xl p-4">
      <h2 className="text-lg font-semibold">Search the catalog</h2>
      <p className="mt-0.5 text-sm text-muted-foreground">
        Survey Notes article full text{nArticles ? ` (${nArticles} articles)` : ""} + every publication
        &amp; map layer by title, keywords, and topic.
      </p>
      <input autoFocus value={q} onChange={(e) => setQ(e.target.value)}
        placeholder="e.g. Moqui marbles, Wasatch fault, gilsonite, geothermal…"
        className="mt-3 w-full rounded-md border border-border bg-background px-3 py-2 text-sm" />

      <label className="mt-2 flex items-center gap-1.5 text-xs text-muted-foreground">
        <input type="checkbox" checked={fullText} onChange={(e) => setFullText(e.target.checked)} />
        Search the <b>full text of every publication</b> (~7000 docs, BM25 — loads a query engine on first use)
      </label>
      <label className="mt-1 flex items-center gap-1.5 text-xs text-muted-foreground">
        <input type="checkbox" checked={semantic} onChange={(e) => setSemantic(e.target.checked)} />
        <b>Semantic</b> — find pubs by meaning, not just words (embeds your query in-browser; first use downloads a small model)
      </label>

      {corpus.isError && <p className="mt-3 text-xs text-muted-foreground">Article full text isn't loaded (built on reingest) — searching catalog metadata only.</p>}

      {raw.length > 0 && (
        <div className="mt-3 flex flex-wrap items-center gap-1.5 text-xs">
          <Chip on={kind === "all"} onClick={() => setKind("all")}>All · {raw.length}</Chip>
          {nArt > 0 && <Chip on={kind === "article"} onClick={() => setKind(kind === "article" ? "all" : "article")}>Articles · {nArt}</Chip>}
          {collFacets.length > 0 && <Chip on={kind === "item"} onClick={() => setKind(kind === "item" ? "all" : "item")}>Publications · {raw.length - nArt}</Chip>}
          {kind !== "article" && collFacets.length > 1 && <span className="mx-0.5 text-muted-foreground">|</span>}
          {kind !== "article" && collFacets.map(([c, n]) => (
            <Chip key={c} on={sel.has(c)} onClick={() => toggleColl(c)}>{c} · {n}</Chip>
          ))}
          {colls.length > 0 && <button className="text-primary hover:underline" onClick={() => setColls([])}>clear</button>}
        </div>
      )}

      {q.trim().length >= 2 && (
        <p className="mt-2 text-xs text-muted-foreground">{results.length} shown</p>
      )}
      <ol className="mt-1 divide-y divide-border">
        {results.map((r) => r.kind === "article"
          ? <ArticleHit key={r.id} r={r} q={q} onOpen={onOpen} />
          : <ItemHit key={r.id} r={r} onOpen={onOpen} />)}
      </ol>

      {fullText && q.trim().length >= 2 && (
        <section className="mt-5">
          <h3 className="text-sm font-semibold">Full text · all publications</h3>
          {pubFts.isLoading && <p className="mt-1 text-xs text-muted-foreground">Loading the query engine + searching…</p>}
          {pubFts.isError && <p className="mt-1 text-xs text-muted-foreground">Full-text index not available yet (built by the FTS job on reingest).</p>}
          {pubFts.data && <p className="mt-1 text-xs text-muted-foreground">{pubFts.data.length} match{pubFts.data.length === 1 ? "" : "es"}</p>}
          <ol className="mt-1 divide-y divide-border">
            {(pubFts.data ?? []).map((r) => (
              <li key={r.id} className="py-2">
                <div className="flex flex-wrap items-baseline gap-x-2">
                  {r.series && <span className="rounded bg-muted px-1.5 text-[10px] uppercase text-muted-foreground">{r.series}</span>}
                  <span className="font-medium text-foreground">{r.title}</span>
                  <span className="font-mono text-xs text-muted-foreground">{r.id}</span>
                </div>
                <div className="mt-1 flex flex-wrap gap-x-3 text-xs">
                  {r.pdf && <a href={r.pdf} target="_blank" rel="noopener" className="text-primary hover:underline">Open PDF ↗</a>}
                  <button className="text-primary hover:underline" onClick={() => onOpen?.(seriesCode(r.id), r.id)}>Catalog page</button>
                </div>
              </li>
            ))}
          </ol>
        </section>
      )}

      {semantic && q.trim().length >= 2 && (
        <section className="mt-5">
          <h3 className="text-sm font-semibold">Semantic · publications by meaning</h3>
          {sem.isLoading && <p className="mt-1 text-xs text-muted-foreground">Embedding the query + searching (first use loads the model)…</p>}
          {sem.isError && <p className="mt-1 text-xs text-muted-foreground">Semantic index not available yet (built by the embed job on reingest).</p>}
          {sem.data && <p className="mt-1 text-xs text-muted-foreground">{sem.data.length} related</p>}
          <ol className="mt-1 divide-y divide-border">
            {(sem.data ?? []).map((r) => (
              <li key={r.pubId} className="py-2">
                <div className="flex flex-wrap items-baseline gap-x-2">
                  {r.series && <span className="rounded bg-muted px-1.5 text-[10px] uppercase text-muted-foreground">{r.series}</span>}
                  <span className="font-medium text-foreground">{r.title}</span>
                  <span className="font-mono text-xs text-muted-foreground">{r.pubId}</span>
                </div>
                {r.snippet && <p className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">…{r.snippet}…</p>}
                <div className="mt-1 flex flex-wrap gap-x-3 text-xs">
                  {r.pdf && <a href={r.pdf} target="_blank" rel="noopener" className="text-primary hover:underline">Open PDF ↗</a>}
                  <button className="text-primary hover:underline" onClick={() => onOpen?.(seriesCode(r.pubId), r.pubId)}>Catalog page</button>
                </div>
              </li>
            ))}
          </ol>
        </section>
      )}
    </div>
  );
}

// Build the combined index (plain function, memoized by the caller).
function buildIndex(articles: Article[], catalog: CatalogDoc[]) {
  const ms = new MiniSearch({
    fields: ["title", "text", "keywords"],
    storeFields: ["kind", "title", "text", "sid", "pdf", "page", "volume", "issue", "collId", "itemId"],
    searchOptions: { boost: { title: 4 }, prefix: true, fuzzy: 0.2, combineWith: "AND" },
  });
  ms.addAll(articles.map((a) => ({ ...a, kind: "article", keywords: "" })));
  ms.addAll(catalog.map((c) => ({
    id: c.id, kind: "item", title: c.title, text: c.meta ?? "", keywords: c.keywords ?? "",
    collId: c.collId, itemId: c.itemId, page: null,
  })));
  return ms;
}

function ArticleHit({ r, q, onOpen }: { r: Hit; q: string; onOpen?: (c: string, i: string) => void }) {
  const pdfHref = r.pdf ? (r.page != null ? `${r.pdf}#page=${r.page}` : r.pdf) : undefined;
  return (
    <li className="py-2">
      <div className="flex flex-wrap items-baseline gap-x-2">
        <span className="rounded bg-muted px-1.5 text-[10px] uppercase text-muted-foreground">article</span>
        <span className="font-medium text-foreground">{r.title}</span>
        <span className="text-xs text-muted-foreground">
          {r.issue || r.sid}{r.volume != null ? ` · Vol ${r.volume}` : ""}{r.page != null ? ` · p. ${r.page}` : ""}
        </span>
      </div>
      <Snippet text={r.text ?? ""} q={q} />
      <div className="mt-1 flex flex-wrap gap-x-3 text-xs">
        {pdfHref && <a href={pdfHref} target="_blank" rel="noopener" className="text-primary hover:underline">
          Open PDF{r.page != null ? ` · p. ${r.page}` : ""} ↗</a>}
        {r.sid && <button className="text-primary hover:underline"
          onClick={() => onOpen?.(seriesCode(r.sid!), r.sid!)}>Catalog page</button>}
      </div>
    </li>
  );
}

function ItemHit({ r, onOpen }: { r: Hit; onOpen?: (c: string, i: string) => void }) {
  return (
    <li className="py-2">
      <div className="flex flex-wrap items-baseline gap-x-2">
        <span className="rounded bg-muted px-1.5 text-[10px] uppercase text-muted-foreground">{r.collId}</span>
        <span className="font-medium text-foreground">{r.title}</span>
        <span className="font-mono text-xs text-muted-foreground">{r.itemId}</span>
      </div>
      {r.text && <p className="mt-0.5 line-clamp-1 text-xs text-muted-foreground">{r.text}</p>}
      <div className="mt-1 text-xs">
        <button className="text-primary hover:underline"
          onClick={() => onOpen?.(r.collId!, r.itemId!)}>Catalog page</button>
      </div>
    </li>
  );
}
