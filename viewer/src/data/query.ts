// Shared search-query grammar — ONE parser reused by every engine so the syntax is identical whether
// you type it inline or build it in the Advanced panel. The parsed `Query` is the single source of
// truth; the text box is just its canonical serialization (parse on type, serialize on panel edit).
//
// Syntax:  bare words (AND) · "exact phrase" · -exclude · -"exclude phrase" · field:value ·
//          field:"multi word" · -field:value.  Known fields below; an unknown `name:value`
//          (a URL, a `1:24000` scale) falls back to a plain term.

export type FieldName = "title" | "keywords" | "topic" | "series" | "coll" | "id";
const FIELDS: readonly FieldName[] = ["title", "keywords", "topic", "series", "coll", "id"];
const FIELD_SET = new Set<string>(FIELDS);

export type FieldClause = { field: FieldName; value: string; negate: boolean };
export type Query = {
  terms: string[];        // bare AND keywords (fuzzy/prefix handled by the engine)
  phrases: string[];      // "quoted" — must appear as a substring
  not: string[];          // -word — must be absent
  notPhrases: string[];   // -"quoted" — must be absent
  fields: FieldClause[];  // field:value (negate = -field:value)
};

type Token = { neg: boolean; field?: string; value: string; quoted: boolean };

function tokenize(input: string): Token[] {
  const out: Token[] = [];
  const n = input.length;
  let i = 0;
  while (i < n) {
    while (i < n && /\s/.test(input[i])) i++;
    if (i >= n) break;
    let neg = false;
    if (input[i] === "-") { neg = true; i++; }
    let field: string | undefined;
    const fm = /^([A-Za-z]+):/.exec(input.slice(i));  // letters + ':' — so `1:24000` / `http://` don't match
    if (fm) { field = fm[1].toLowerCase(); i += fm[0].length; }
    let value: string;   // assigned in both branches below
    let quoted = false;
    if (input[i] === '"') {
      quoted = true; i++;
      const end = input.indexOf('"', i);
      if (end === -1) { value = input.slice(i); i = n; }
      else { value = input.slice(i, end); i = end + 1; }
    } else {
      const start = i;
      while (i < n && !/\s/.test(input[i])) i++;
      value = input.slice(start, i);
    }
    if (value !== "" || field) out.push({ neg, field, value, quoted });
  }
  return out;
}

export function parseQuery(input: string): Query {
  const q: Query = { terms: [], phrases: [], not: [], notPhrases: [], fields: [] };
  for (const t of tokenize(input)) {
    if (t.field && FIELD_SET.has(t.field)) {
      q.fields.push({ field: t.field as FieldName, value: t.value, negate: t.neg });
    } else if (t.field) {
      // unknown field → keep the literal `name:value` as a plain term (URLs, scales, etc.)
      (t.neg ? q.not : q.terms).push(`${t.field}:${t.value}`);
    } else if (t.quoted) {
      if (t.value) (t.neg ? q.notPhrases : q.phrases).push(t.value);
    } else if (t.value) {
      (t.neg ? q.not : q.terms).push(t.value);
    }
  }
  return q;
}

export const isEmptyQuery = (q: Query): boolean =>
  !q.terms.length && !q.phrases.length && !q.not.length && !q.notPhrases.length && !q.fields.length;

/** The plain words to hand a keyword engine (bare terms + the words inside phrases), for narrowing.
 *  Empty string → a field/exclude-only query (caller should match against the full doc set). */
export function baseTerms(q: Query): string {
  return [...q.terms, ...q.phrases.flatMap((p) => p.split(/\s+/))].filter(Boolean).join(" ");
}

// ---- matching (MiniSearch post-filter / field-only path) ----

export type SearchDoc = {
  title?: string; text?: string; keywords?: string; topic?: string;
  series?: string; collId?: string; itemId?: string; id?: string;
};

const seriesOf = (sid: string) => sid.match(/^[A-Za-z]+/)?.[0]?.toUpperCase() ?? sid;

function fieldValue(d: SearchDoc, f: FieldName): string {
  switch (f) {
    case "title": return d.title ?? "";
    case "keywords": return d.keywords ?? "";
    case "topic": return d.topic ?? "";
    case "series": return d.series ?? seriesOf(d.itemId ?? d.id ?? "");
    case "coll": return d.collId ?? "";
    case "id": return d.itemId ?? d.id ?? "";
  }
}

/** Post-filter a candidate doc. Bare `terms` are intentionally NOT re-checked here — the keyword
 *  engine already ANDed them (with prefix/fuzzy); re-checking as substrings would drop good matches.
 *  This enforces the precise parts: phrases, exclusions, and field constraints. */
export function matchesQuery(q: Query, d: SearchDoc): boolean {
  const hay = [d.title, d.text, d.keywords, d.topic, d.itemId].filter(Boolean).join(" ").toLowerCase();
  for (const p of q.phrases) if (!hay.includes(p.toLowerCase())) return false;
  for (const t of q.not) if (hay.includes(t.toLowerCase())) return false;
  for (const p of q.notPhrases) if (hay.includes(p.toLowerCase())) return false;
  for (const f of q.fields) {
    const dv = fieldValue(d, f.field).toLowerCase();
    const v = f.value.toLowerCase();
    // series/coll are exact codes (series:M must NOT match MP/MF); text fields are substring.
    const hit = f.field === "series" || f.field === "coll" ? dv === v : dv.includes(v);
    if (f.negate ? hit : !hit) return false;
  }
  return true;
}

// ---- BM25 (DuckDB) wiring ----

const sqlLit = (s: string) => s.replace(/'/g, "''");

/** Words for `match_bm25` (bare terms + phrase words). */
export function bm25Terms(q: Query): string {
  return [...q.terms, ...q.phrases.flatMap((p) => p.split(/\s+/))]
    .map((w) => w.toLowerCase().match(/[\p{L}\p{N}]+/gu)?.join(" ") ?? "")
    .filter(Boolean).join(" ");
}

/** Extra SQL predicates (AND-ed onto the WHERE) for phrases, exclusions, and the `series` field.
 *  `textCols` are the doc columns to LIKE-scan for phrase/exclude (discovered at runtime). */
export function bm25Where(q: Query, textCols: string[]): string[] {
  const preds: string[] = [];
  const like = (needle: string, negate: boolean) => {
    const cols = textCols.map((c) => `lower(d.${c}) LIKE '%${sqlLit(needle.toLowerCase())}%'`);
    if (!cols.length) return null;
    return negate ? `NOT (${cols.join(" OR ")})` : `(${cols.join(" OR ")})`;
  };
  for (const p of q.phrases) { const c = like(p, false); if (c) preds.push(c); }
  for (const t of q.not) { const c = like(t, true); if (c) preds.push(c); }
  for (const p of q.notPhrases) { const c = like(p, true); if (c) preds.push(c); }
  for (const f of q.fields) {
    if (f.field === "series") {
      preds.push(`${f.negate ? "NOT " : ""}(lower(d.series) = '${sqlLit(f.value.toLowerCase())}')`);
    } else if (f.field === "id") {
      preds.push(`${f.negate ? "NOT " : ""}(lower(d.id) LIKE '%${sqlLit(f.value.toLowerCase())}%')`);
    } else if (f.field === "title") {
      preds.push(`${f.negate ? "NOT " : ""}(lower(d.title) LIKE '%${sqlLit(f.value.toLowerCase())}%')`);
    }
    // topic/keywords/coll aren't columns in the FTS db → silently ignored on this engine.
  }
  return preds;
}
