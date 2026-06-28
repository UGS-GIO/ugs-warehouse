// Unified search — client-side, no server. Two sources, one in-browser MiniSearch index:
//   • Survey Notes article FULL TEXT (the warehouse-built corpus, pubs/search/corpus.json)
//   • EVERY catalog item's metadata (title / id / keywords / topic) — pubs, maps, vector layers
// so a query hits article bodies AND any publication or layer. Results link the PDF page and the
// in-app catalog detail.
import { useQuery } from "@tanstack/react-query";
import MiniSearch from "minisearch";
import { useMemo, useState } from "react";

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
  const corpus = useCorpus(true);
  const index = useMemo(() => buildIndex(corpus.data ?? [], catalog), [corpus.data, catalog]);
  const results = useMemo(() => (q.trim().length < 2 ? [] : index.search(q).slice(0, 60) as unknown as Hit[]), [index, q]);

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

      {corpus.isError && <p className="mt-3 text-xs text-muted-foreground">Article full text isn't loaded (built on reingest) — searching catalog metadata only.</p>}

      {q.trim().length >= 2 && (
        <p className="mt-3 text-xs text-muted-foreground">{results.length} result{results.length === 1 ? "" : "s"}</p>
      )}
      <ol className="mt-1 divide-y divide-border">
        {results.map((r) => r.kind === "article"
          ? <ArticleHit key={r.id} r={r} q={q} onOpen={onOpen} />
          : <ItemHit key={r.id} r={r} onOpen={onOpen} />)}
      </ol>
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
