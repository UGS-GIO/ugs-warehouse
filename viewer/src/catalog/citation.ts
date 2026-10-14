// Citation text, BibTeX and RIS for a publication, built only from the properties the catalog
// publishes. Nothing is invented: a missing field is left out.

export type CitationFields = {
  sid: string;
  title: string;
  authors: string[];
  // True when `authors` is one literal string that could not be split safely.
  authorsAmbiguous: boolean;
  year?: string;
  pubType?: string;
  doi?: string;
  citation?: string;
};

const PUBLISHER = "Utah Geological Survey";
const str = (v: unknown): string | undefined => (typeof v === "string" && v.trim() ? v.trim() : undefined);

export type Authors = { names: string[]; ambiguous: boolean };

const INITIALS = /^(?:[A-Z]\.\s*)+$|^[A-Z]$/;
const PERSON_SUFFIX = /^(?:Jr|Sr|II|III|IV)\.?$/i;
const ORG_SUFFIX = /^(?:Inc|Co|Corp|Ltd|LLC)\.?$/i;
// Parentheses, notes, years, "&" and credit phrases mean the string is prose, not a name list.
const PROSE = /[()?\d&]|\b(?:assisted|modified|compiled|by)\b/i;

// Splits the feed's `ugs:author` string. Handles ";" lists, "First Last, First Last, and First Last",
// and inverted "Last, F.M., Last, F." lists. Anything else comes back whole with `ambiguous` set,
// for a single literal author, rather than a guessed split.
export const splitAuthors = (raw: string | undefined): Authors => {
  const whole = (raw ?? "").trim().replace(/\s+/g, " ");
  if (!whole) return { names: [], ambiguous: false };
  const literal = { names: [whole], ambiguous: true };
  if (PROSE.test(whole)) return literal;
  const names: string[] = [];
  for (const seg of whole.split(/;/).flatMap((x) => x.split(/,?\s+and\s+/i))) {
    const text = seg.trim().replace(/^and\s+/i, "").replace(/^,+|,+$/g, "").trim();
    if (!text) continue;
    // Fold suffix tokens into the name before them.
    const toks: { t: string; suffix?: string }[] = [];
    for (const t of text.split(",").map((x) => x.trim()).filter(Boolean)) {
      const prev = toks[toks.length - 1];
      if (prev && PERSON_SUFFIX.test(t)) prev.suffix = t;
      else if (prev && ORG_SUFFIX.test(t)) prev.t = `${prev.t}, ${t}`;
      else toks.push({ t });
    }
    const initials = toks.slice(1).some((k) => INITIALS.test(k.t));
    if (initials) {
      for (let i = 0; i < toks.length; i += 2) {
        const last = toks[i], init = toks[i + 1];
        if (!init || INITIALS.test(last.t) || !INITIALS.test(init.t)) return literal;
        const sfx = init.suffix ?? last.suffix;
        names.push([last.t, sfx, init.t].filter(Boolean).join(", "));
      }
    } else if (toks.length === 1) {
      names.push(toks[0].t + (toks[0].suffix ? ` ${toks[0].suffix}` : ""));
    } else if (toks.length === 2 && toks.every((k) => /^[^\s.]+$/.test(k.t))) {
      names.push(`${toks[0].t}, ${toks[1].t}`);
    } else if (toks.every((k) => /\s/.test(k.t))) {
      for (const k of toks) names.push(k.t + (k.suffix ? ` ${k.suffix}` : ""));
    } else return literal;
  }
  return { names, ambiguous: false };
};

export const citationFields = (props: Record<string, unknown> | undefined, id?: string): CitationFields => {
  const p = props ?? {};
  const sid = str(p["ugs:series_id"]) ?? id ?? "";
  const dt = str(p.datetime);
  const year = !p["ugs:date_unknown"] && dt && /^\d{4}/.test(dt) ? dt.slice(0, 4) : undefined;
  const au = splitAuthors(str(p["ugs:author"]));
  return {
    sid,
    title: str(p.title) ?? sid,
    authors: au.names,
    authorsAmbiguous: au.ambiguous,
    year,
    pubType: str(p["ugs:pub_type"]),
    doi: str(p["sci:doi"]) ?? (sid ? `10.34191/${sid}` : undefined),
    citation: str(p["sci:citation"]),
  };
};

const doiUrl = (f: CitationFields) => (f.doi ? `https://doi.org/${f.doi}` : undefined);
const isMap = (f: CitationFields) => /\bmap\b/i.test(f.pubType ?? "");

const joinAuthors = (a: string[]) =>
  a.length <= 2 ? a.join(" and ") : `${a.slice(0, -1).join(", ")}, and ${a[a.length - 1]}`;

// The catalog's own citation when it has one; otherwise "<Authors>, <year>, <title>: Utah Geological
// Survey <type> <id>, <doi url>." from the fields present.
export const plainCitation = (f: CitationFields): string => {
  if (f.citation) return f.citation;
  const head = [joinAuthors(f.authors), f.year, f.title].filter(Boolean).join(", ");
  const series = [PUBLISHER, f.pubType, f.sid].filter(Boolean).join(" ");
  return [`${head}: ${series}`, doiUrl(f)].filter(Boolean).join(", ") + ".";
};

const BIB_SPECIALS: Record<string, string> = {
  "\\": "\\textbackslash{}", "{": "\\{", "}": "\\}", "&": "\\&", "%": "\\%", "$": "\\$",
  "#": "\\#", "_": "\\_", "~": "\\textasciitilde{}", "^": "\\textasciicircum{}",
};
export const bibEscape = (s: string): string => s.replace(/[\\{}&%$#_~^]/g, (c) => BIB_SPECIALS[c]);

export const bibtex = (f: CitationFields): string => {
  const misc = isMap(f);
  const author = f.authorsAmbiguous ? `{${bibEscape(f.authors[0])}}` : f.authors.map(bibEscape).join(" and ");
  // doi and url are verbatim in BibTeX, so they skip escaping.
  const fields: [string, string | undefined, boolean][] = [
    ["author", author || undefined, false],
    ["title", f.title, true],
    ["year", f.year, true],
    // @misc styles ignore `institution`, so maps carry the publisher in `howpublished`.
    misc ? ["howpublished", [PUBLISHER, f.pubType, f.sid].filter(Boolean).join(" "), true]
      : ["institution", PUBLISHER, true],
    ["type", f.pubType, true],
    ["number", f.sid, true],
    ["doi", f.doi, false],
    ["url", doiUrl(f), false],
  ];
  const body = fields.filter(([, v]) => v)
    .map(([k, v, esc]) => `  ${k} = {${esc ? bibEscape(v as string) : v}}`).join(",\n");
  const key = `UGS_${f.sid}`.replace(/[^A-Za-z0-9_:.-]/g, "_");
  return `@${misc ? "misc" : "techreport"}{${key},\n${body}\n}\n`;
};

export const ris = (f: CitationFields): string => {
  const lines: [string, string | undefined][] = [
    ["TY", isMap(f) ? "MAP" : "RPRT"],
    ...f.authors.map((a): [string, string] => ["AU", a]),
    ["TI", f.title],
    ["PY", f.year],
    ["PB", PUBLISHER],
    ["SN", f.sid],
    ["DO", f.doi],
    ["UR", doiUrl(f)],
  ];
  // A line break inside a value would start a new RIS tag.
  const flat = (v: string) => v.replace(/\s+/g, " ").trim();
  return [...lines.filter(([, v]) => v).map(([k, v]) => `${k}  - ${flat(v as string)}`), "ER  - "].join("\r\n") + "\r\n";
};
