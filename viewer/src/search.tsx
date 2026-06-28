// Full-text article search — client-side, no server. Loads the warehouse-built corpus
// (pubs/search/corpus.json: one entry per Survey Notes article, text sliced by TOC page range)
// once, builds a MiniSearch index in the browser, and searches it. Results deep-link the PDF page.
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

function buildIndex(corpus: Article[]): MiniSearch<Article> {
  const ms = new MiniSearch<Article>({
    fields: ["title", "text", "issue"],
    storeFields: ["sid", "volume", "issue", "pdf", "title", "page", "text"],
    searchOptions: { boost: { title: 4, issue: 2 }, prefix: true, fuzzy: 0.2, combineWith: "AND" },
  });
  ms.addAll(corpus);
  return ms;
}

/** Lazy-load + index the corpus (gated on `enabled` so it only fetches when the Search view opens). */
function useArticleIndex(enabled: boolean) {
  return useQuery({
    queryKey: ["article-corpus", CORPUS_URL],
    enabled,
    staleTime: Infinity,
    retry: false,
    queryFn: async () => {
      const r = await fetch(CORPUS_URL);
      if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
      const corpus = (await r.json()) as Article[];
      return { index: buildIndex(corpus), count: corpus.length };
    },
  });
}

// The data-series code (alpha prefix) of an id — the viewer's leaf collection id (SNT-58-2 → SNT).
const seriesCode = (sid: string) => sid.match(/^[A-Za-z]+/)?.[0]?.toUpperCase() ?? sid;

// A snippet around the first query-term hit, with the matched terms highlighted.
function Snippet({ text, q }: { text: string; q: string }) {
  const terms = q.toLowerCase().split(/\s+/).filter((t) => t.length > 1);
  const lower = text.toLowerCase();
  let at = -1;
  for (const t of terms) { const i = lower.indexOf(t); if (i >= 0) { at = i; break; } }
  const start = at < 0 ? 0 : Math.max(0, at - 60);
  const body = (start ? "…" : "") + text.slice(start, start + 220).trim() + "…";
  // Split on the terms (longest first) and wrap matches in <mark>.
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
const esc = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

export function ArticleSearch({ onOpen }: { onOpen?: (collId: string, itemId: string) => void }) {
  const [q, setQ] = useState("");
  const { data, isLoading, error } = useArticleIndex(true);
  const results = useMemo(() => {
    if (!data || q.trim().length < 2) return [];
    return data.index.search(q, { prefix: true, fuzzy: 0.2 }).slice(0, 50) as unknown as (Article & { score: number })[];
  }, [data, q]);

  return (
    <div className="mx-auto max-w-3xl p-4">
      <h2 className="text-lg font-semibold">Search Survey Notes articles</h2>
      <p className="mt-0.5 text-sm text-muted-foreground">
        Full text of every parsed issue — {data ? `${data.count} articles` : "loading…"}. Results link the PDF page.
      </p>
      <input autoFocus value={q} onChange={(e) => setQ(e.target.value)}
        placeholder="e.g. Moqui marbles, Wasatch fault, gilsonite…"
        className="mt-3 w-full rounded-md border border-border bg-background px-3 py-2 text-sm" />

      {error && <p className="mt-3 text-sm text-destructive">Couldn't load the search corpus ({String((error as Error).message)}). It's built on reingest.</p>}
      {isLoading && <p className="mt-3 text-sm text-muted-foreground">Building index…</p>}

      {q.trim().length >= 2 && data && (
        <p className="mt-3 text-xs text-muted-foreground">{results.length} result{results.length === 1 ? "" : "s"}</p>
      )}
      <ol className="mt-1 divide-y divide-border">
        {results.map((r) => {
          const pdfHref = r.pdf ? (r.page != null ? `${r.pdf}#page=${r.page}` : r.pdf) : undefined;
          return (
            <li key={r.id} className="py-2">
              <div className="flex flex-wrap items-baseline gap-x-2">
                <span className="font-medium text-foreground">{r.title}</span>
                <span className="text-xs text-muted-foreground">
                  {r.issue || r.sid}{r.volume != null ? ` · Vol ${r.volume}` : ""}{r.page != null ? ` · p. ${r.page}` : ""}
                </span>
              </div>
              <Snippet text={r.text} q={q} />
              <div className="mt-1 flex flex-wrap gap-x-3 text-xs">
                {pdfHref && <a href={pdfHref} target="_blank" rel="noopener" className="text-primary hover:underline">
                  Open PDF{r.page != null ? ` · p. ${r.page}` : ""} ↗</a>}
                {/* Catalog page: the issue's detail in the viewer (in-app nav, no reload). */}
                <button className="text-primary hover:underline"
                  onClick={() => onOpen?.(seriesCode(r.sid), r.sid)}>Catalog page</button>
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
