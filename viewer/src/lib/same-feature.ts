// Whether a map feature and a table row are the same record, by their shared attribute values.
//
// The map and the table link through `feature_id`, which is only an ordinal: two copies of a layer
// from different ingests (a saved area and the live table, or a cached file) number rows
// differently, and the same id then names another record. Comparing values catches that.

const SKIP = /^(feature_id|geom|geometry|bbox(_[xy](min|max))?)$/;

const numEq = (a: number, b: number) => Math.abs(a - b) <= 1e-6 * Math.max(1, Math.abs(a), Math.abs(b));

/** False only when a shared attribute differs; true when nothing is comparable (nothing to go on). */
export function sameFeature(a: Record<string, unknown>, b: Record<string, unknown>): boolean {
  for (const [k, va] of Object.entries(a)) {
    const vb = b[k];
    if (SKIP.test(k) || va == null || vb == null || typeof va === "object" || typeof vb === "object") continue;
    const na = Number(va), nb = Number(vb);
    const same = typeof va === "string" && typeof vb === "string" ? va === vb
      : Number.isFinite(na) && Number.isFinite(nb) ? numEq(na, nb) : String(va) === String(vb);
    if (!same) return false;
  }
  return true;
}
