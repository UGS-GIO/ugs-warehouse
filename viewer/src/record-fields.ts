// Which fields a record card leads with. Pure so it can be tested without a table.
//
// Column order is the wrong answer: `enmin_ccus_cbgeoregion` starts id/maps/ogc_fid, so a
// position-based preview shows the same number three times and hides the basin's ranking and
// reservoirs. Rank by what tells one record from another instead.

// Plumbing rather than content: ingest bookkeeping, join keys, and geometry measures. Also what an
// opened card tucks into its "technical" group, so the fields a reader came for stay together.
const SYSTEM = /^_|^id$|_id$|(^|_)fid$|^target_epsg$|^table_type$|^review_status$|^shape?_|^shp_|^scale$|^quad_name$/;

export const isTechnical = (name: string): boolean => SYSTEM.test(name);

const isEmpty = (v: unknown): boolean => v == null || v === "" || (typeof v === "string" && !v.trim());
// A value with letters names something; a bare number rarely does on its own.
const isWords = (v: unknown): boolean => typeof v === "string" && /[a-z]/i.test(v);

// `constant` = this column holds the same value in every row on the page (`maps` is 0 throughout
// the CCUS regions). It can't tell one record from another, whatever it contains.
export function scoreField(name: string, value: unknown, constant = false): number {
  let score = 0;
  if (SYSTEM.test(name)) score -= 5;
  if (isEmpty(value)) score -= 4;
  if (constant) score -= 3;
  if (isWords(value)) score += 2;
  return score;
}

/** Column names whose value never changes across the given rows. */
export function constantFields(rows: readonly (readonly { name: string; value: unknown }[])[]): Set<string> {
  const seen = new Map<string, Set<string>>();
  for (const row of rows) {
    for (const f of row) {
      const values = seen.get(f.name) ?? new Set<string>();
      values.add(String(f.value));
      seen.set(f.name, values);
    }
  }
  return new Set([...seen].filter(([, values]) => values.size <= 1).map(([name]) => name));
}

/** Indices of the fields to show collapsed, best first, falling back to column order on ties. */
export function previewOrder(
  fields: readonly { name: string; value: unknown }[],
  count: number,
  constant: ReadonlySet<string> = new Set(),
): number[] {
  return fields
    .map((f, i) => ({ i, score: scoreField(f.name, f.value, constant.has(f.name)) }))
    .sort((a, b) => b.score - a.score || a.i - b.i)
    .slice(0, count)
    .map((f) => f.i);
}
