// The GeoParquet columns the explorer treats specially, and making values JSON-safe. Shared by the
// DuckDB path (download.ts) and the light hyparquet path (parquet-lite.ts), and kept apart from
// both so the light path does not load the export code.

export const GEOM_NAMES = ["geom", "geometry", "wkb_geometry"];

// The plain numeric bbox covering columns the warehouse writes (sink_archive). Read for row→map
// zoom, hidden from the displayed table (noise) like the geometry column.
export const BBOX_COLS = ["bbox_xmin", "bbox_ymin", "bbox_xmax", "bbox_ymax"];

// Stable per-row id the transform stamps (1..N, hilbert order) into BOTH the GeoParquet and the
// PMTiles (as the MVT feature id). It's the join key for map↔table linking. Hidden from the
// displayed columns (synthetic noise) but kept on each row object so the table can highlight a
// row the map picked, and used as a deterministic ORDER BY tiebreaker under a user sort so OFFSET
// paging and the feature_id→ordinal lookup agree exactly. Because it is stamped in hilbert order,
// it also matches file order — which is why the unsorted page can drop the sort entirely.
export const ID_COL = "feature_id";

// Arrow rows carry BigInt (int64 cols) + Dates; make them JSON-safe.
const MAX_SAFE = BigInt(Number.MAX_SAFE_INTEGER);
export const sanitize = (v: unknown): unknown => {
  // BigInt within JS's safe range → Number; beyond it → string, so huge ids keep exact precision.
  if (typeof v === "bigint") return v <= MAX_SAFE && v >= -MAX_SAFE ? Number(v) : v.toString();
  return v instanceof Date ? v.toISOString() : v;
};
