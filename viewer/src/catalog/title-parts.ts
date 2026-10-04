// A publication title split into the part that tells it apart ("Moab-12 quadrangle") and the rest.
// Display only; a title no pattern fits returns null and is shown whole rather than guessed at.
export type TitleParts = { lead: string; kind: string; where: string };

// "<kind> of the <X quadrangle(s)>, <counties>"
const QUAD = /^(?<kind>.+?) of (?:the )?(?<lead>[^,]+?quadrangles?)(?:,\s*(?<where>.+))?$/i;
// "<kind> of <place>, <X County/Counties>, Utah"
const PLACE = /^(?<kind>.+?) of (?:the )?(?<lead>[^,]+?),\s*(?<where>.*\b(?:County|Counties|Utah)\b.*)$/i;
// "<lead>: <subtitle>" or "<lead> - <subtitle>"
const SUBTITLE = /^(?<lead>[^:]+?)(?::| -) (?<kind>.+)$/;

const cap = (s: string) => (s ? s[0].toUpperCase() + s.slice(1) : s);

const tidyWhere = (w: string) => w.split(/,\s*with |\s+-\s+/)[0].trim().replace(/(^|,\s*)Utah$/, "");

export function titleParts(title: string): TitleParts | null {
  const t = title.trim();
  const m = QUAD.exec(t) ?? PLACE.exec(t);
  if (m?.groups) {
    const { kind, lead, where = "" } = m.groups;
    // "...landfill sites for City of Moab": the "of" belongs to the place name, not the kind.
    if (/\bcity$/i.test(kind)) return null;
    return { lead: cap(lead.trim()), kind: cap(kind.trim()), where: tidyWhere(where) };
  }
  const s = SUBTITLE.exec(t);
  if (s?.groups) {
    let { lead, kind } = s.groups;
    // "Utah Mining - 2023 Metals, ..." puts the year that tells the editions apart after the dash.
    const year = /^(\d{4})\s+(.+)$/.exec(kind);
    if (year) { lead = `${lead} ${year[1]}`; kind = year[2]; }
    return { lead: cap(lead.trim()), kind: cap(kind.trim()), where: "" };
  }
  return null;
}

export const isInterim = (title: string): boolean => /^interim\b/i.test(title.trim());
