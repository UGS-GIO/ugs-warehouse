// Survey Notes article search — the corpus fetch and the article result row, extracted from the
// standalone /search page so Discover can render article hits beside its catalog results.
import { useQuery } from "@tanstack/react-query";
import { qk } from "@/query-keys";

import type { Article, Hit } from "./search-index";

export const CORPUS_URL = new URL(
  new URLSearchParams(location.search).get("searchCorpus")
    || "https://maps-assets.geology.utah.gov/pubs/search/corpus.json",
  location.href,
).href;

const esc = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

export function useCorpus(enabled: boolean) {
  return useQuery({
    queryKey: qk.articleCorpus(CORPUS_URL),
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

export function ArticleHit({ r, q, openPub }: { r: Hit; q: string; openPub: (id: string) => void }) {
  const pdfHref = r.pdf ? (r.page != null ? `${r.pdf}#page=${r.page}` : r.pdf) : undefined;
  return (
    <li className="py-2">
      <div className="flex flex-wrap items-baseline gap-x-2">
        <span className="rounded bg-muted px-1.5 text-xs uppercase text-muted-foreground">article</span>
        {r.topic && <span className="rounded bg-primary/10 px-1.5 text-xs text-primary">{r.topic}</span>}
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
          onClick={() => openPub(r.sid!)}>Catalog page</button>}
      </div>
    </li>
  );
}
