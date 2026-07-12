// _review ↔ _current layer diff — client-side via duckdb-wasm, no server. Compares two GeoParquets
// (the pre-release review version vs the live current version of the same topic) and reports what
// changed: added / removed / modified / unchanged features.
//
// KEY CHOICE — match by GEOMETRY, not feature_id. feature_id is a row-number in hilbert order
// (transform.py), so it shifts when any feature is added/removed/moved — useless as a cross-version
// identity. A geometry hash (`md5(geom)` over the raw WKB) is stable: same geometry → same feature.
// A geometry *edit* therefore shows as remove+add (correct for a diff; move-tracking via the source
// PK is a future enhancement).
import { openParquet } from "./duckdb";

// The public (current) GeoParquet always lives on the CDN under warehouse/geoparquet/<stem>/, whatever
// deploy we're on. Overridable via ?currentcdn= for testing against a different host.
export const CURRENT_CDN = new URL(
  new URLSearchParams(location.search).get("currentcdn") || "https://maps-assets.geology.utah.gov",
  location.href,
).href.replace(/\/$/, "");

/** URL of the CURRENT (live) GeoParquet for a topic stem — the diff baseline. */
export function currentGeoparquetUrl(stem: string): string {
  return `${CURRENT_CDN}/warehouse/geoparquet/${stem}/${stem}.parquet`;
}

// Warehouse/ingest-injected columns — EXCLUDED from the attribute comparison. `geom` is the join key;
// `feature_id` shifts; `review_status` differs by design (Y vs R) so it'd flag every row; the rest are
// derived (h3/bbox) or injected metadata. Everything else = the source attributes we actually diff.
const WAREHOUSE_COLS = new Set([
  "feature_id", "geom", "h3_r9", "bbox_xmin", "bbox_ymin", "bbox_xmax", "bbox_ymax",
  "target_epsg", "metadata_publication_id", "quad_name", "review_status", "scale", "table_type",
]);

export type DiffSummary = {
  reviewRows: number;
  currentRows: number;
  added: number;      // geometry in review, not in current
  removed: number;    // geometry in current, not in review
  modified: number;   // same geometry, source attributes differ
  unchanged: number;  // same geometry + same source attributes
  sourceCols: string[]; // the columns actually compared (for display / drill-down)
};

const qi = (s: string) => `"${s.replace(/"/g, '""')}"`;

/** Diff a review-version GeoParquet against its current-version counterpart. Both are HTTP URLs
 * (review = the IAP app, same-origin; current = the public CDN). Returns the change summary. */
export async function diffLayers(reviewUrl: string, currentUrl: string): Promise<DiffSummary> {
  const conn = await openParquet({ "review.parquet": reviewUrl, "current.parquet": currentUrl });

  // Source attribute columns = everything not warehouse-injected (discovered from the review side).
  const desc = await conn.query(`DESCRIBE SELECT * FROM read_parquet('review.parquet')`);
  const cols = desc.toArray().map((r) => String(r.column_name));
  const src = cols.filter((c) => !WAREHOUSE_COLS.has(c.toLowerCase()));

  // Attribute fingerprint = a hash over the source columns, so a change in any of them flips `attr`.
  const attr = src.length
    ? `md5(${src.map((c) => `COALESCE(CAST(${qi(c)} AS VARCHAR), '∅')`).join(" || '¦' || ")})`
    : `'∅'`;
  // Collapse dup geometries per side (rare — stacked points) so the FULL OUTER JOIN can't explode.
  const side = (file: string) =>
    `SELECT gk, any_value(attr) AS attr FROM (
        SELECT md5(geom) AS gk, ${attr} AS attr FROM read_parquet('${file}')
     ) GROUP BY gk`;

  const sql = `
    WITH r AS (${side("review.parquet")}), c AS (${side("current.parquet")}),
    j AS (SELECT r.gk AS rgk, c.gk AS cgk, r.attr AS ra, c.attr AS ca
          FROM r FULL OUTER JOIN c ON r.gk = c.gk)
    SELECT
      (SELECT count(*) FROM read_parquet('review.parquet'))  AS review_rows,
      (SELECT count(*) FROM read_parquet('current.parquet')) AS current_rows,
      count(*) FILTER (WHERE cgk IS NULL)                                   AS added,
      count(*) FILTER (WHERE rgk IS NULL)                                   AS removed,
      count(*) FILTER (WHERE rgk IS NOT NULL AND cgk IS NOT NULL AND ra <> ca) AS modified,
      count(*) FILTER (WHERE rgk IS NOT NULL AND cgk IS NOT NULL AND ra =  ca) AS unchanged
    FROM j`;

  const row = (await conn.query(sql)).toArray()[0];
  const num = (v: unknown) => Number(v ?? 0);
  return {
    reviewRows: num(row.review_rows),
    currentRows: num(row.current_rows),
    added: num(row.added),
    removed: num(row.removed),
    modified: num(row.modified),
    unchanged: num(row.unchanged),
    sourceCols: src,
  };
}
