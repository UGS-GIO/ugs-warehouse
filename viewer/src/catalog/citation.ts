// Citation text, BibTeX and RIS for a publication, built only from the properties the catalog
// publishes. Nothing is invented: a missing field is left out.

export type CitationFields = {
  sid: string;
  title: string;
  authors: string[];
  year?: string;
  pubType?: string;
  doi?: string;
  citation?: string;
};

const PUBLISHER = "Utah Geological Survey";
const str = (v: unknown): string | undefined => (typeof v === "string" && v.trim() ? v.trim() : undefined);

// The feed's author string mixes ";" separators, " and ", and comma-joined "First Last" lists.
// A segment with exactly one comma is "Last, First" and kept as is; one with more is a list.
export const splitAuthors = (raw: string | undefined): string[] =>
  (raw ?? "").split(/;|\band\b/i).flatMap((seg) => {
    const s = seg.trim().replace(/^,+|,+$/g, "").trim();
    if (!s) return [];
    const parts = s.split(",").map((x) => x.trim()).filter(Boolean);
    return parts.length > 2 ? parts : [parts.join(", ")];
  });

export const citationFields = (props: Record<string, unknown> | undefined, id?: string): CitationFields => {
  const p = props ?? {};
  const sid = str(p["ugs:series_id"]) ?? id ?? "";
  const dt = str(p.datetime);
  const year = !p["ugs:date_unknown"] && dt && /^\d{4}/.test(dt) ? dt.slice(0, 4) : undefined;
  return {
    sid,
    title: str(p.title) ?? sid,
    authors: splitAuthors(str(p["ugs:author"])),
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
  const fields: [string, string | undefined][] = [
    ["author", f.authors.length ? f.authors.join(" and ") : undefined],
    ["title", f.title],
    ["year", f.year],
    ["institution", PUBLISHER],
    ["type", f.pubType],
    ["number", f.sid],
    ["doi", f.doi],
    ["url", doiUrl(f)],
  ];
  const body = fields.filter(([, v]) => v).map(([k, v]) => `  ${k} = {${bibEscape(v as string)}}`).join(",\n");
  const key = `UGS_${f.sid}`.replace(/[^A-Za-z0-9_:.-]/g, "_");
  return `@${isMap(f) ? "misc" : "techreport"}{${key},\n${body}\n}\n`;
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
  return [...lines.filter(([, v]) => v).map(([k, v]) => `${k}  - ${v}`), "ER  - "].join("\r\n") + "\r\n";
};
