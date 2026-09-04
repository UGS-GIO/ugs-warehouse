// Unified search — client-side, no server. Two sources, one in-browser MiniSearch index:
//   • Survey Notes article FULL TEXT (the warehouse-built corpus, pubs/search/corpus.json)
//   • EVERY catalog item's metadata (title / id / keywords / topic) — pubs, maps, vector layers
// so a query hits article bodies AND any publication or layer. Results link the PDF page and the
// in-app catalog detail.
import { useQuery } from "@tanstack/react-query";
import { type ReactNode, useMemo, useState } from "react";

import { searchPubs } from "./ftsearch";
import {
  baseTerms, fieldInput, isEmptyQuery, matchesQuery, parseQuery, type Query,
  type SearchDoc, serializeQuery, withExcludes, withField, withSinglePhrase, withTerms,
} from "@/data/query";
import { type Article, buildIndex, type CatalogDoc, type Hit } from "./search-index";

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

// Article / CatalogDoc / Hit + buildIndex now live in ./search-index (imported above) so the Discover
// view builds the SAME index; consumers import those types from ./search-index.

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
  const [adv, setAdv] = useState(false);  // Advanced panel open/closed
  const [kind, setKind] = useState<"all" | "article" | "item">("all");
  const [colls, setColls] = useState<string[]>([]);
  const [topicSel, setTopicSel] = useState<string | null>(null);  // Survey Notes article topic filter
  const [fullText, setFullText] = useState(false);
  // Heavy full-text-of-every-pub search (duckdb-wasm over the remote FTS db) — only runs when the
  // toggle is on, and only re-queries when the text settles (manual trigger via the query key).
  const pubFts = useQuery({
    queryKey: ["pubfts", q],
    queryFn: () => searchPubs(q),
    enabled: fullText && q.trim().length >= 2,
    staleTime: 60_000, retry: false,
  });
  const corpus = useCorpus(true);
  // Resolve a pub's real collection from the loaded catalog (itemId → collId). The FTS db only
  // carry the series code, which is NOT a collection id — opening the catalog page needs the actual
  // collection (ugs-publications / -external / -mining-district-files). seriesCode is a last resort.
  const collOf = useMemo(() => {
    const m = new Map<string, string>();
    for (const c of catalog) m.set(c.itemId.toUpperCase(), c.collId);
    return m;
  }, [catalog]);
  const openPub = (id: string) => onOpen?.(collOf.get(id.toUpperCase()) ?? seriesCode(id), id);
  const { index, docs: allDocs } = useMemo(() => buildIndex(corpus.data ?? [], catalog), [corpus.data, catalog]);
  // Smart Advanced-panel options — the real series/collections/topics present in what's loaded,
  // ranked by frequency (so the common ones surface first in the typeahead).
  const facetOptions = useMemo<FacetOptions>(() => {
    const ranked = (arr: string[]) => {
      const m = new Map<string, number>();
      for (const s of arr) if (s) m.set(s, (m.get(s) ?? 0) + 1);
      return [...m.entries()].sort((a, b) => b[1] - a[1]).map(([k]) => k);
    };
    return {
      series: ranked(catalog.map((c) => seriesCode(c.itemId))),
      colls: ranked(catalog.map((c) => c.collId)),
      topics: ranked((corpus.data ?? []).map((a) => a.topic ?? "")),
    };
  }, [catalog, corpus.data]);
  // Parse the box into the shared Query; the Advanced panel below is just another editor of it.
  const query = useMemo(() => parseQuery(q), [q]);
  const raw = useMemo(() => {
    if (q.trim().length < 2 && isEmptyQuery(query)) return [];
    const base = baseTerms(query);
    // Bare/phrase words → narrow via MiniSearch; a field/exclude-only query → scan the full doc set.
    const cand = base ? (index.search(base) as unknown as Hit[]) : allDocs;
    return cand.filter((h) => matchesQuery(query, h as SearchDoc)).slice(0, 300);
  }, [index, allDocs, q, query]);

  // Facets over the current matches: the publication collections present (for the item filter).
  const collFacets = useMemo(() => {
    const m = new Map<string, number>();
    for (const r of raw) if (r.kind === "item" && r.collId) m.set(r.collId, (m.get(r.collId) ?? 0) + 1);
    return [...m.entries()].sort((a, b) => b[1] - a[1]);
  }, [raw]);
  // Topics present among the matching Survey Notes articles (now that each article is classified).
  const topicFacets = useMemo(() => {
    const m = new Map<string, number>();
    for (const r of raw) if (r.kind === "article" && r.topic) m.set(r.topic, (m.get(r.topic) ?? 0) + 1);
    return [...m.entries()].sort((a, b) => b[1] - a[1]);
  }, [raw]);
  const nArt = raw.filter((r) => r.kind === "article").length;
  const sel = new Set(colls);
  const toggleColl = (c: string) => setColls(sel.has(c) ? colls.filter((x) => x !== c) : [...colls, c]);

  const results = useMemo(() => raw.filter((r) => {
    if (kind !== "all" && r.kind !== kind) return false;
    if (r.kind === "item" && colls.length && !(r.collId && sel.has(r.collId))) return false;
    if (r.kind === "article" && topicSel && r.topic !== topicSel) return false;
    return true;
  }).slice(0, 60), [raw, kind, colls, topicSel]);  // eslint-disable-line react-hooks/exhaustive-deps

  const nArticles = corpus.data?.length ?? 0;
  return (
    <div className="mx-auto max-w-[75ch] p-4">
      <h2 className="text-lg font-semibold">Full-text search</h2>
      <p className="mt-0.5 text-sm text-muted-foreground">
        Survey Notes article full text{nArticles ? ` (${nArticles} articles)` : ""} + every publication
        &amp; map layer by title, keywords, and topic.
      </p>
      <input autoFocus value={q} onChange={(e) => setQ(e.target.value)}
        placeholder="e.g. Moqui marbles, Wasatch fault, gilsonite, geothermal…"
        className="mt-3 w-full rounded-md border border-border bg-background px-3 py-2 text-sm" />

      <div className="mt-1 flex items-center justify-between text-xs text-muted-foreground">
        <span>
          Tips: <code className="rounded bg-muted px-1">"exact phrase"</code> ·{" "}
          <code className="rounded bg-muted px-1">-exclude</code> ·{" "}
          <code className="rounded bg-muted px-1">series:GQ</code> ·{" "}
          <code className="rounded bg-muted px-1">topic:geothermal</code>
        </span>
        <button onClick={() => setAdv((v) => !v)} className="text-primary hover:underline">
          {adv ? "Hide advanced" : "Advanced"}
        </button>
      </div>

      {adv && <AdvancedPanel q={q} onChange={setQ} options={facetOptions} />}

      <label className="mt-2 flex items-center gap-1.5 text-xs text-muted-foreground">
        <input type="checkbox" checked={fullText} onChange={(e) => setFullText(e.target.checked)} />
        Search the <b>full text of every publication</b> (~7000 docs, BM25 — loads a query engine on first use)
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

      {kind !== "item" && topicFacets.length > 1 && (
        <div className="mt-2 flex flex-wrap items-center gap-1.5 text-xs">
          <span className="mr-0.5 text-xs uppercase tracking-wide text-muted-foreground">Article topic</span>
          {topicFacets.map(([t, n]) => (
            <Chip key={t} on={topicSel === t} onClick={() => setTopicSel(topicSel === t ? null : t)}>{t} · {n}</Chip>
          ))}
          {topicSel && <button className="text-primary hover:underline" onClick={() => setTopicSel(null)}>clear</button>}
        </div>
      )}

      {q.trim().length >= 2 && (
        <p className="mt-2 text-xs text-muted-foreground">{results.length} shown</p>
      )}
      <ol className="mt-1 divide-y divide-border">
        {results.map((r) => r.kind === "article"
          ? <ArticleHit key={r.id} r={r} q={q} openPub={openPub} />
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
                  {r.series && <span className="rounded bg-muted px-1.5 text-xs uppercase text-muted-foreground">{r.series}</span>}
                  <span className="font-medium text-foreground">{r.title}</span>
                  <span className="font-mono text-xs text-muted-foreground">{r.id}</span>
                </div>
                <div className="mt-1 flex flex-wrap gap-x-3 text-xs">
                  {r.pdf && <a href={r.pdf} target="_blank" rel="noopener" className="text-primary hover:underline">Open PDF ↗</a>}
                  <button className="text-primary hover:underline" onClick={() => openPub(r.id)}>Catalog page</button>
                </div>
              </li>
            ))}
          </ol>
        </section>
      )}

    </div>
  );
}

// One labelled input in the Advanced panel. With `options`, it's a typeahead combobox (native
// datalist) over the real values loaded from the catalog — smart, but still free-entry.
function Row({ label, value, placeholder, onChange, options }: {
  label: string; value: string; placeholder?: string; onChange: (v: string) => void;
  options?: string[];
}) {
  const listId = options ? `dl-${label.replace(/\s+/g, "-")}` : undefined;
  return (
    <label className="flex flex-col gap-0.5">
      <span className="text-xs font-medium text-muted-foreground">{label}</span>
      <input value={value} placeholder={placeholder} list={listId}
        onChange={(e) => onChange(e.target.value)}
        className="rounded border border-border bg-background px-2 py-1 text-sm" />
      {options && <datalist id={listId}>{options.map((o) => <option key={o} value={o} />)}</datalist>}
    </label>
  );
}

// Structured editor over the SAME query string (parse in, serialize out) — two-way synced with the
// box because the box's text is the single source of truth. Edit here → box updates, and vice versa.
type FacetOptions = { series: string[]; colls: string[]; topics: string[] };

function AdvancedPanel({ q, onChange, options }: {
  q: string; onChange: (s: string) => void; options: FacetOptions;
}) {
  const query = parseQuery(q);
  const set = (next: Query) => onChange(serializeQuery(next));
  return (
    <div className="mt-2 grid grid-cols-1 gap-2 rounded-md border border-border bg-card/50 p-3 sm:grid-cols-2">
      <Row label="All of these words" value={query.terms.join(" ")} placeholder="fault geothermal"
        onChange={(v) => set(withTerms(query, v))} />
      <Row label="Exact phrase" value={query.phrases[0] ?? ""} placeholder="Wasatch fault"
        onChange={(v) => set(withSinglePhrase(query, v))} />
      <Row label="Exclude words" value={query.not.join(" ")} placeholder="uinta"
        onChange={(v) => set(withExcludes(query, v))} />
      <Row label="Series" value={fieldInput(query, "series")} placeholder="GQ, M, OFR…"
        options={options.series} onChange={(v) => set(withField(query, "series", v.toUpperCase()))} />
      <Row label="Topic" value={fieldInput(query, "topic")} placeholder="geothermal"
        options={options.topics} onChange={(v) => set(withField(query, "topic", v))} />
      <Row label="Collection" value={fieldInput(query, "coll")} placeholder="ugs-publications"
        options={options.colls} onChange={(v) => set(withField(query, "coll", v))} />
    </div>
  );
}

function ArticleHit({ r, q, openPub }: { r: Hit; q: string; openPub: (id: string) => void }) {
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

function ItemHit({ r, onOpen }: { r: Hit; onOpen?: (c: string, i: string) => void }) {
  return (
    <li className="py-2">
      <div className="flex flex-wrap items-baseline gap-x-2">
        <span className="rounded bg-muted px-1.5 text-xs uppercase text-muted-foreground">{r.collId}</span>
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
